"""Is the fuel-mail threshold any good? Measured against labelled senders.

``oilwatch/quote_judge.py`` asks a model whether an unrecognised sender's mail
reads like a fuel quote, and ``fuel_mail_min_probability`` decides how high that
probability must be before the sweep names the domain in its log. The threshold
arrived as a dataclass default, and nothing has ever measured it: there is no
labelled set in the repository, and no code reads the verdicts back.

It can be measured anyway, with no key, no model and no network call, because the
sweep has already stored one verdict per sender it judged. What was missing was
the labels, and the tracked supplier register supplies them: a domain the register
chases sells heating oil, so its mail is exactly what the judgement must not miss,
and a domain in its ``excluded_domains`` is one the sweep must never mistake for a
supplier. ``sender_judgements`` holds the probability the model returned for each,
so the two halves join on the domain and answer the question directly — does this
threshold separate them?

    python tools/fuel_mail_calibration.py
    python tools/fuel_mail_calibration.py --check

A sample that cannot decide anything is reported as such and never as a pass.
``--check`` exits non-zero only on a CONTRADICTION — a register supplier judged
below the threshold, or an excluded domain judged at or above it — and says
plainly when the sample merely fails to constrain the threshold, which is what an
unexercised threshold looks like rather than evidence that it is right.

**Only register-derived domains are ever printed.** ``sender_judgements`` is one
row per correspondent the sweep could not place, which is the correspondence
``tools/build_snapshot.py`` exists to keep out of this repository, so this reports
counts, the register's own names, and nothing else.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oilwatch.config import CHECKOUT_ROOT  # noqa: E402

REGISTER = CHECKOUT_ROOT / "config" / "suppliers.json"

#: How near the threshold a verdict has to sit before the sample can be said to
#: constrain it. The probabilities are not calibrated, so "near the line" cannot
#: be derived from the scores themselves; a fifth of the interval is stated rather
#: than discovered, and it is printed beside the count so a reader can disagree
#: with it without rerunning anything.
MARGIN = 0.2


@dataclass
class Calibration:
    """What the stored verdicts say about one threshold."""

    threshold: float
    judged: int = 0
    minimum: float | None = None
    maximum: float | None = None
    at_or_above: int = 0
    within_margin: int = 0
    supplier_below: list[tuple[str, float]] = field(default_factory=list)
    supplier_at_or_above: list[tuple[str, float]] = field(default_factory=list)
    excluded_at_or_above: list[tuple[str, float]] = field(default_factory=list)
    excluded_below: list[tuple[str, float]] = field(default_factory=list)

    @property
    def contradictions(self) -> list[tuple[str, float]]:
        """Judgements the threshold gets wrong on the labels that exist.

        Both directions are reported, and they are different faults: a register
        supplier read as not-fuel is the miss this judgement was written to stop
        (a genuine quote in a shape no pattern reads), while an excluded domain
        read as fuel is the alert firing on a directory site.
        """
        return self.supplier_below + self.excluded_at_or_above

    @property
    def constrained(self) -> bool:
        """Whether any verdict sits close enough to the threshold to test it."""
        return self.within_margin > 0

    @property
    def labelled(self) -> int:
        """Judgements that carry a register label, in either direction."""
        return (
            len(self.supplier_below)
            + len(self.supplier_at_or_above)
            + len(self.excluded_at_or_above)
            + len(self.excluded_below)
        )


def host_of(url: str) -> str:
    """The host in a website value, lower-cased and ``www.``-stripped."""
    host = url.split("//")[-1].split("/")[0].strip().lower()
    return host.removeprefix("www.")


def register_labels(register: dict) -> tuple[set[str], set[str]]:
    """``(supplier hosts, excluded domains)`` from the tracked register."""
    suppliers = {host_of(str(s.get("website") or "")) for s in register.get("suppliers") or []}
    excluded = {str(d).strip().lower() for d in register.get("excluded_domains") or []}
    return {h for h in suppliers if h}, {d for d in excluded if d}


def _labelled_as(domain: str, labels: set[str]) -> str | None:
    """The label this sender domain carries, or ``None``.

    Deliberately loose about ``www.`` and about subdomains, because the register
    records a website and the judgement records the domain a message came from —
    ``valleyfuels.com``, ``www.valleyfuels.com`` and ``email.valleyfuels.com`` are
    one supplier, and a register that names ``oilweb.johnstonfuels.co.uk`` is still
    the same company as ``johnstonfuels.co.uk``. The looseness can only ever *add*
    a label, so its worst case is a contradiction reported that a strict comparison
    would have missed, never a contradiction hidden.

    A key with no dot in it carries no label by construction: ``sender_judgements``
    is also where a ``From`` header that would not parse ends up (the live table
    holds dozens of single-token keys), and matching one of those against a
    register host would label a stranger.
    """
    domain = host_of(domain)
    if "." not in domain:
        return None
    for label in labels:
        if domain == label or domain.endswith(f".{label}"):
            return label
        if label.endswith(f".{domain}") and label.count(".") == domain.count(".") + 1:
            return label
    return None


def read_verdicts(database: Path) -> list[tuple[str, float]]:
    """``(domain, probability)`` for every sender the sweep has judged."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT domain, fuel_probability FROM sender_judgements"
        ).fetchall()
    finally:
        connection.close()
    return [(str(domain or ""), float(probability)) for domain, probability in rows]


def calibrate(
    verdicts: list[tuple[str, float]],
    threshold: float,
    suppliers: set[str],
    excluded: set[str],
    *,
    margin: float = MARGIN,
) -> Calibration:
    """Score one threshold against the register's labels."""
    result = Calibration(threshold=threshold, judged=len(verdicts))
    for domain, probability in verdicts:
        if result.minimum is None or probability < result.minimum:
            result.minimum = probability
        if result.maximum is None or probability > result.maximum:
            result.maximum = probability
        if probability >= threshold:
            result.at_or_above += 1
        if abs(probability - threshold) <= margin:
            result.within_margin += 1
        if _labelled_as(domain, suppliers):
            # Named by the register, so this is a label we can print.
            target = (
                result.supplier_at_or_above if probability >= threshold else result.supplier_below
            )
            target.append((domain, probability))
        elif _labelled_as(domain, excluded):
            target = (
                result.excluded_at_or_above if probability >= threshold else result.excluded_below
            )
            target.append((domain, probability))
    return result


def report(result: Calibration, *, suppliers: int, excluded: int) -> None:
    """Print what the sample says, including that it may say nothing."""
    span = (
        "no verdicts" if result.minimum is None else f"{result.minimum:.4g} to {result.maximum:.4g}"
    )
    print(f"register   {suppliers} supplier domain(s), {excluded} excluded domain(s)")
    print(f"verdicts   {result.judged} judged, {span}")
    print(f"threshold  {result.threshold:.4g}")
    print(f"margin     {MARGIN:.4g} - verdict(s) within it: {result.within_margin}")
    print(f"labels     {result.labelled} verdict(s) carry a register label")
    print(f"named      {result.at_or_above} of {result.judged} at or above the threshold")

    if result.contradictions:
        print("result     CONTRADICTED - the threshold mislabels verdicts it can be checked on:")
        for domain, probability in result.supplier_below:
            print(f"             {domain} {probability:.4g} - a register supplier, not named")
        for domain, probability in result.excluded_at_or_above:
            print(f"             {domain} {probability:.4g} - excluded, but would be named")
        return

    if not result.constrained:
        print(
            "result     UNCONSTRAINED - no verdict lies within "
            f"{MARGIN:.4g} of the threshold, so every value in the empty span beside it "
            "decides these verdicts identically."
        )
        print(
            "           That is not a pass: the threshold is unexercised here, not "
            "validated. It is contradicted by no verdict because none is near it."
        )
        return

    print(
        "result     CONSISTENT - the verdicts near the threshold all carry the label "
        "the threshold gives them."
    )


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the fuel-mail threshold against the register."
    )
    parser.add_argument("--database", type=Path, default=None, help="the live database")
    parser.add_argument("--threshold", type=float, default=None, help="probability to score")
    parser.add_argument("--register", type=Path, default=REGISTER, help="the supplier register")
    parser.add_argument("--check", action="store_true", help="exit non-zero on a contradiction")
    args = parser.parse_args(argv)

    register = load_json(args.register)
    suppliers, excluded = register_labels(register)
    threshold = args.threshold
    database = args.database
    if threshold is None or database is None:
        settings = load_json(CHECKOUT_ROOT / "config" / "settings.json")
        if threshold is None:
            threshold = float(settings.get("fuel_mail_min_probability", 0.8))
        if database is None:
            database = CHECKOUT_ROOT / str(settings["database_path"])

    if not Path(database).exists():
        print(f"database   {database} does not exist, so there is nothing to measure")
        return 0

    result = calibrate(read_verdicts(Path(database)), threshold, suppliers, excluded)
    report(result, suppliers=len(suppliers), excluded=len(excluded))
    return 1 if (args.check and result.contradictions) else 0


if __name__ == "__main__":
    sys.exit(main())
