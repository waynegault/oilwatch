from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from oilwatch.config import Settings
from oilwatch.geo import GeoService
from oilwatch.models import SupplierCandidate


PRICE_WORDS = ("heating oil", "kerosene", "fuel", "domestic oil", "gas oil")
LOCAL_HINTS = (
    "aberdeenshire",
    "aberdeen",
    "inverurie",
    "ellon",
    "huntly",
    "peterhead",
    "stonehaven",
    "banff",
    "turriff",
    "deeside",
)
UK_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(\+44\s?\d[\d\s]{7,}|\(?0\d[\d\s]{8,}\d)")


class DiscoveryService:
    def __init__(self, settings: Settings, geo: GeoService) -> None:
        self.settings = settings
        self.geo = geo
        self.client = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": "OilWatch/0.1 (+https://github.com/)"},
            timeout=20.0,
        )

    def home_coordinates(self) -> tuple[float, float]:
        if self.settings.home.latitude is not None and self.settings.home.longitude is not None:
            return (self.settings.home.latitude, self.settings.home.longitude)
        location = self.geo.geocode(self.settings.home.label)
        if not location:
            raise RuntimeError(f"Unable to geocode home location: {self.settings.home.label}")
        return (location[0], location[1])

    def discover(self) -> list[SupplierCandidate]:
        home = self.home_coordinates()
        candidates: dict[str, SupplierCandidate] = {}
        for query in self.settings.search_queries:
            html = self._search(query)
            for candidate in self._parse_results(query, html):
                domain = urlparse(candidate.website).netloc.lower()
                if domain in candidates:
                    continue
                self._enrich_candidate(candidate, home)
                if candidate.distance_miles is not None and candidate.distance_miles <= self.settings.radius_miles:
                    candidate.status = "active"
                elif candidate.distance_miles is None and self._appears_local(candidate):
                    candidate.status = "active"
                    candidate.notes = (candidate.notes + " " if candidate.notes else "") + "Distance not geocoded; accepted from local search context."
                candidates[domain] = candidate
        return [candidate for candidate in candidates.values() if candidate.status == "active"]

    def _search(self, query: str) -> str:
        response = self.client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query, "kl": "uk-en"},
        )
        response.raise_for_status()
        return response.text

    def _parse_results(self, query: str, html: str) -> list[SupplierCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[SupplierCandidate] = []
        for result in soup.select("div.result"):
            anchor = result.select_one("a.result__a")
            snippet_node = result.select_one("a.result__snippet")
            if not anchor:
                continue
            website = self._unwrap_duckduckgo_url(anchor.get("href", ""))
            if not website:
                continue
            domain = urlparse(website).netloc.lower()
            if self._is_excluded_domain(domain):
                continue
            title = anchor.get_text(" ", strip=True)
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            haystack = f"{title} {snippet}".lower()
            if not any(word in haystack for word in PRICE_WORDS):
                continue
            results.append(
                SupplierCandidate(
                    name=self._supplier_name_from_title(title, domain),
                    website=website,
                    query=query,
                    title=title,
                    snippet=snippet,
                )
            )
        return results

    def _enrich_candidate(self, candidate: SupplierCandidate, home: tuple[float, float]) -> None:
        page_text = ""
        try:
            response = self.client.get(candidate.website)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            candidate.email = self._extract_email(str(soup))
            candidate.phone = self._extract_phone(page_text)
            candidate.address = self._extract_address(page_text)
        except Exception as exc:  # noqa: BLE001
            candidate.notes = f"Enrichment limited: {exc}"

        geocode = self.geo.geocode_first(
            [
                candidate.address or "",
                f"{candidate.name}, Aberdeenshire, Scotland",
                f"{candidate.name}, Aberdeen, Scotland",
                f"{candidate.title}, Scotland",
            ]
        )
        if not geocode:
            if page_text:
                candidate.notes = (candidate.notes + " " if candidate.notes else "") + "Could not geocode supplier."
            return

        candidate.latitude = geocode[0]
        candidate.longitude = geocode[1]
        if not candidate.address:
            candidate.address = geocode[2]
        candidate.distance_miles = round(
            self.geo.distance_miles(home, (candidate.latitude, candidate.longitude)),
            2,
        )

    def _is_excluded_domain(self, domain: str) -> bool:
        return any(domain == excluded or domain.endswith(f".{excluded}") for excluded in self.settings.excluded_domains)

    @staticmethod
    def _unwrap_duckduckgo_url(url: str) -> str:
        if "uddg=" not in url:
            return url
        query = parse_qs(urlparse(url).query)
        values = query.get("uddg")
        if not values:
            return ""
        return unquote(values[0])

    @staticmethod
    def _supplier_name_from_title(title: str, domain: str) -> str:
        generic_terms = ("home", "heating oil", "fuel", "quote", "prices", "local")
        for separator in ("|", " - ", " — "):
            if separator not in title:
                continue
            parts = [part.strip() for part in title.split(separator) if part.strip()]
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                if first.lower() == "home" or first.lower().startswith("heating oil in"):
                    return last
                if any(term in first.lower() for term in generic_terms) and not any(term in last.lower() for term in generic_terms):
                    return last
                return first
        return domain.split(".")[0].replace("-", " ").title()

    @staticmethod
    def _extract_email(text: str) -> str | None:
        match = re.search(r"mailto:([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", text, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _extract_phone(text: str) -> str | None:
        match = PHONE_RE.search(text)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_address(text: str) -> str | None:
        match = UK_POSTCODE_RE.search(text)
        if not match:
            return None
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 40)
        return text[start:end].strip()

    @staticmethod
    def _appears_local(candidate: SupplierCandidate) -> bool:
        haystack = " ".join(
            [
                candidate.query,
                candidate.title,
                candidate.snippet,
                candidate.address or "",
                candidate.notes,
            ]
        ).lower()
        return any(keyword in haystack for keyword in LOCAL_HINTS)
