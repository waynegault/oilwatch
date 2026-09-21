"""Is this message fuel mail? A semantic judgement, asked of TypeSafe's Jev.

The sweep names an unrecognised sender only when the mail reads like a fuel
quote. That guard exists because naming every unknown sender put eighty of the
owner's correspondents on one log line, including a financial ombudsman case and
his bank. The test behind it was ``extract_ppl(...) is not None`` — a price regex
standing in for a judgement about meaning — so a genuine quote in a format no
pattern covers read as "not fuel", and the one case the alert exists to catch was
the one it stayed quiet about.

This module asks the question directly. ``extract_ppl`` stays the first test
because it is free and exact; when it finds nothing, a [Noul] question — does this
read like a fuel quote? — comes back as a probability that the caller thresholds.

[Noul]: https://docs.typesafe.ai/primitives/noul

**Fail-soft, always.** The sweep is unattended: no key, no network, a timeout or a
malformed response returns ``None`` and the caller keeps the behaviour it had. A
judgement that cannot be made must never break the sweep, and ``None`` means "not
judged" rather than "not fuel" — the two must not be confused.

**One judgement per sender, not per message.** Unrecognised mail is deliberately
left in the mailbox rather than deleted, so the same messages are re-read every
hour; asking per message would spend a request on each of them, every sweep,
forever. The caller caches the verdict per domain (see ``db.sender_judgement``).
"""

from __future__ import annotations

import os
import sys

import httpx

from oilwatch.logging_setup import get_logger

log = get_logger("quote_judge")

#: One endpoint serves every TypeSafe model; the request's ``model`` field picks
#: which one.
API_URL = "https://api.typesafe.ai/v1/systemone"

#: The alias the TypeSafe docs use and the SDK default. The response reports the
#: versioned id that actually answered (``jev-1.13.0``), so a change behind the
#: alias is visible in the log rather than silent.
MODEL = "jev-latest"

#: The key is read from the process environment, then from the Windows user
#: environment. See :func:`_api_key`.
ENV_VAR = "TYPESAFE_API_KEY"

#: Deliberately short. This judgement is a bonus on a message the price parser
#: already failed to read, and the sweep runs hourly: waiting on it is worse than
#: going without it, because a cached ``None`` is retried on the next sweep.
DEFAULT_TIMEOUT = 15.0

#: How much of a message is judged. What the mail *is* is legible from its
#: opening; the rest is footer, unsubscribe links and the sender's own history.
#: TypeSafe bills input tokens, so this bounds the cost of a request too.
STATE_CHARS = 4000

#: The question id is for this caller's own bookkeeping — the model never sees it,
#: so the meaning has to be complete in ``instructions``.
QUESTION_ID = "fuel_quote"

INSTRUCTIONS = (
    "Does this message read like a quote, a stated price, or a discount offer for "
    "domestic heating oil from a fuel supplier?"
)

#: The two answers, named. Jev returns a probability rather than a label, so saying
#: what counts as a ``true`` and what counts as a ``false`` is what keeps "unrelated
#: mail-out" from drifting into "anything vaguely commercial".
#:
#: An object with these two keys, not a string: the API rejects a string here with a
#: 422, and the fail-soft contract below turns that into a plain ``None`` — so the
#: judgement would have gone missing silently rather than loudly.
CRITERIA = {
    "true": (
        "It states a price per litre, a total for a quantity of heating oil, or an "
        "offer or discount on a fuel order — including from an oil company not seen "
        "before."
    ),
    "false": (
        "Personal correspondence, a bank or utility statement, a delivery notice "
        "for something other than fuel, or a newsletter carrying no fuel price or "
        "offer."
    ),
}


def _key_from_windows_environment() -> str | None:
    """The key as held in the Windows user environment, or ``None``.

    ``HKCU\\Environment`` is the canonical store: it is where the key was put and
    what ``oc refresh-keys`` reads. A process started by Task Scheduler or at
    logon inherits it, but one launched through WSL interop does not — WSL hands
    the Windows process its own environment instead — and neither does a process
    whose parent started before the key was set.

    Reading the registry covers exactly those cases, and covers them without
    keeping a second copy of the secret anywhere: the value is read where it
    already lives, at the moment it is needed.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - winreg is in the Windows stdlib
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            value, _ = winreg.QueryValueEx(handle, ENV_VAR)
    except OSError:  # pragma: no cover - no such value, or no access to it
        return None
    return (value or "").strip() or None


def _api_key() -> str | None:
    """The TypeSafe API key, or ``None`` when this install has none."""
    key = (os.environ.get(ENV_VAR) or "").strip()
    return key or _key_from_windows_environment()


def fuel_mail_probability(
    text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> float | None:
    """How likely this message is a fuel quote, or ``None`` if unjudgeable.

    ``None`` means "no judgement available" — never "not fuel mail". A caller
    must keep its old behaviour in that case rather than read it as a verdict,
    which is why the failure paths all return the same thing.

    ``client`` lets a caller supply its own ``httpx.Client``; without one, a
    single request is made and closed.
    """
    if not text or not text.strip():
        return None
    key = _api_key()
    if key is None:
        log.debug("%s is not set; the fuel-mail judgement is unavailable", ENV_VAR)
        return None

    payload = {
        "state": text[:STATE_CHARS],
        "model": MODEL,
        "questions": {
            QUESTION_ID: {
                "type": "noul",
                "instructions": INSTRUCTIONS,
                "criteria": CRITERIA,
            }
        },
    }
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if client is not None:
            response = client.post(API_URL, json=payload, headers=headers, timeout=timeout)
        else:
            response = httpx.post(API_URL, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        answer = float(response.json()["answers"][QUESTION_ID]["noul"])
    except Exception as exc:  # noqa: BLE001 - any failure is simply "no judgement"
        # Including a KeyError or a TypeError from a reshaped response: the
        # contract with the caller is a probability or None, and a response this
        # module does not understand is not a probability.
        log.warning(
            "the fuel-mail judgement could not be made (%s); the price parser stands alone",
            exc,
        )
        return None
    if not 0.0 <= answer <= 1.0:
        log.warning("the fuel-mail judgement returned %r, outside 0..1; ignored", answer)
        return None
    return answer
