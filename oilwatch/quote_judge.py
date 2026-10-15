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

Fail-soft is not the same as fail-blind, though, and the two failures are told
apart in the log even though they return the same thing: *not answered* (no key,
no route, a timeout) is a request that did not get through, while *answered with
something that is not a probability* is a contract that moved. The second is the
one worth waking up for, and it is the reason the reader below is strict about the
TYPE and not only the value: ``float()`` accepts ``"0.9"`` and ``true`` (as 1.0),
so a reshaped response would otherwise be read as a confident verdict.

**One judgement per sender per batch of mail.** Unrecognised mail is deliberately
left in the mailbox rather than deleted, so the same messages are re-read every
hour; asking per message would spend a request on each of them, every sweep,
forever. The caller caches the verdict per domain (see ``db.sender_judgement``),
and caches what that verdict *accounted for* alongside it: a sender already being
named keeps its name, while one judged below the threshold is judged again when
mail it has not seen arrives. A verdict that covered everything it was ever shown
would make a sender's newsletter answer for its quote — which is the one case this
module was written to catch.
"""

from __future__ import annotations

import os
import re
import sys

import httpx

from oilwatch.logging_setup import get_logger

log = get_logger("quote_judge")

#: One endpoint serves every TypeSafe model; the request's ``model`` field picks
#: which one.
API_URL = "https://api.typesafe.ai/v1/systemone"

#: The alias the TypeSafe docs use and the SDK default. An alias is not a version:
#: it can be re-pointed at a new one without a line changing here, and because the
#: threshold below is applied to whatever probability that version returns, a bump
#: would move every judgement while the record stayed identical. So each judgement
#: logs the versioned id the response names (``jev-1.13.0``) — see
#: :func:`_resolved_model` for why that id is looked for rather than read from a
#: pinned field.
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

#: A versioned identifier — a name, a hyphen, then a digit (``jev-1.13.0``). Used
#: to *find* the id the service answered under, never to validate it: the point is
#: that a re-pointed alias shows up in the log, not that the service uses a
#: particular spelling.
_MODEL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*-[0-9][A-Za-z0-9_.\-]*$")


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


class _MalformedAnswer(Exception):
    """The service answered, but not with a probability.

    Separate from every other failure because it means something different: the
    request got through and the answer is not the shape this module was written
    against, which is a contract that moved rather than a judgement that could
    not be made.
    """


def _resolved_model(payload: object) -> str | None:
    """The versioned model id the response names, or ``None`` when it names none.

    Looked for rather than read from a named field, deliberately: nothing in this
    repository records the response's schema, and inventing a field name would
    turn a guess into a silent ``None``. What matters here is only that a re-pointed
    alias is *visible*, so any string shaped like a versioned id in the body or in
    this question's answer is reported; the alias itself is the risk, not the key.
    """
    if not isinstance(payload, dict):
        return None
    candidates: list[object] = list(payload.values())
    answers = payload.get("answers")
    if isinstance(answers, dict):
        question = answers.get(QUESTION_ID)
        if isinstance(question, dict):
            candidates.extend(question.values())
    for value in candidates:
        if isinstance(value, str) and _MODEL_ID.match(value.strip()):
            return value.strip()
    return None


def _read_answer(payload: object) -> float:
    """The probability in a response body, or :class:`_MalformedAnswer`.

    Strict about the type as well as the value. ``noul`` is a JSON number and the
    four shapes below are the whole contract; ``float()`` on the raw field would
    instead accept ``"0.9"`` and ``True`` — a reshaped response read as a
    confident verdict, which is the one error this module exists to avoid.
    """
    if not isinstance(payload, dict):
        raise _MalformedAnswer(f"a {type(payload).__name__} body in place of an object")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise _MalformedAnswer("no answers object")
    answer = answers.get(QUESTION_ID)
    if not isinstance(answer, dict) or "noul" not in answer:
        raise _MalformedAnswer(f"no noul for the {QUESTION_ID!r} question")
    value = answer["noul"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _MalformedAnswer(f"{value!r} ({type(value).__name__}) in place of a number")
    probability = float(value)
    if not 0.0 <= probability <= 1.0:
        raise _MalformedAnswer(f"{probability!r}, outside 0..1")
    return probability


def fuel_mail_probability(
    text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> float | None:
    """How likely this message is a fuel quote, or ``None`` if unjudgeable.

    ``None`` means "no judgement available" — never "not fuel mail". A caller
    must keep its old behaviour in that case rather than read it as a verdict,
    which is why the failure paths all return the same thing: the log tells the
    two apart (nothing answered, or answered with something that is not a
    probability), and the return value deliberately does not.

    ``client`` lets a caller supply its own ``httpx.Client``; without one, a
    single request is made and closed.
    """
    if not text or not text.strip():
        return None
    key = _api_key()
    if key is None:
        log.debug("%s is not set; the fuel-mail judgement is unavailable", ENV_VAR)
        return None

    request = {
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
    body: object
    try:
        if client is not None:
            response = client.post(API_URL, json=request, headers=headers, timeout=timeout)
        else:
            response = httpx.post(API_URL, json=request, headers=headers, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        answer = _read_answer(body)
    except _MalformedAnswer as exc:
        # The service answered; it just did not answer with a probability. Named
        # apart from the branch below because it is the shape worth acting on: a
        # request that did not get through is weather, a contract that moved is
        # work. Both are still "no judgement" to the caller.
        log.warning(
            "the fuel-mail judgement answered with %s, which is not a probability; "
            "read as no judgement",
            exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - any failure is simply "no judgement"
        # The request that did not get through: no route, a refused status, a
        # timeout, or a body that is not JSON at all. A body that IS JSON but is
        # not the answer's shape is the branch above, deliberately - the two read
        # the same to the caller and must not read the same in the log.
        log.warning(
            "the fuel-mail judgement could not be made (%s); the price parser stands alone",
            exc,
        )
        return None
    resolved = _resolved_model(body)
    log.info(
        "the fuel-mail judgement answered %.4f%s",
        answer,
        f" (model {resolved})" if resolved else "",
    )
    return answer
