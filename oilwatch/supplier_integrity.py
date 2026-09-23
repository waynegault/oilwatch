"""Detecting one supplier recorded as two rows.

``Database.upsert_supplier`` resolves conflicts on ``website`` alone, and the
schema makes that column UNIQUE. A supplier whose website moves is therefore
inserted *again* rather than updated: the new host is a new key, so no existing
row matches and the old row survives beside the new one. A Turriff Fuels twin
appeared exactly that way on 2026-09-22 — the register was repointed at
``www.turrifffuels.com`` and a second row was created next to the
``turriff-fuels.co.uk`` row, each holding part of the quote history.

The write path now refuses to build such a twin: :meth:`Database.upsert_supplier`
raises :class:`SupplierIdentityConflict` when the incoming record's name is
already recorded against a different website, so the second write stops and the
operator reconciles instead of the history splitting. That refusal decides
nothing — it only declines to guess. This module reports the pairs that predate
the refusal, or that arrived by some other route.

:func:`duplicate_supplier_groups` reports them and never merges them. Which rows
are genuinely the same supplier is an operator's call, not a name comparison's,
and a wrong merge destroys quote history silently. So only two signals are
treated as decisive, and only when they are identical once normalised:

* the name, folded for case, punctuation and whitespace
* the contact email address, folded for case

A near miss is deliberately not reported. A missed duplicate costs one manual
look; a false one invites a bad merge.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: Anything that is not a letter or digit is folded away, so "Turriff Fuels",
#: "turriff-fuels" and " TURRIFF  FUELS " are one name.
_NAME_NOISE = re.compile(r"[^a-z0-9]+")


def normalise_name(name: object) -> str:
    """Fold a supplier name to the form two rows must share to be a pair."""
    return _NAME_NOISE.sub("", str(name or "").casefold())


def normalise_email(email: object) -> str:
    """Fold a contact address, which is compared case-insensitively."""
    return str(email or "").strip().casefold()


class SupplierIdentityConflict(RuntimeError):
    """A supplier's name is already recorded on another website.

    Raised by :meth:`oilwatch.db.Database.upsert_supplier` instead of inserting a
    second row. The write stops because which website is the real one is the
    operator's call; a name comparison that repointed the existing row would
    merge two companies silently, which is what this module exists to prevent.
    """


def conflicting_name_matches(
    suppliers: Iterable[dict[str, Any]],
    name: object,
    website: object,
) -> list[dict[str, Any]]:
    """The recorded rows that share ``name`` but sit on a different website.

    A row on the *same* website is the ordinary update case and is not returned,
    so an empty result means the write can proceed. The name is folded exactly as
    the reporting check folds it, so the refusal and the report agree on what
    "the same name" means.

    Only the name is decisive here, where the report also keys on the email: two
    trading names can legitimately share one mailbox, so an email is a reason to
    look at a pair, not a reason to refuse a write.
    """
    key = normalise_name(name)
    if not key:
        return []
    site = str(website or "")
    return [
        row
        for row in suppliers
        if normalise_name(row.get("name")) == key and str(row.get("website") or "") != site
    ]


def describe_identity_conflict(conflicts: Iterable[dict[str, Any]]) -> str:
    """Name each conflicting row by id and website, oldest id first."""
    return "; ".join(
        f"id {row.get('id')} ({row.get('website')})"
        for row in sorted(conflicts, key=_by_id)
    )


def _summary(supplier: dict[str, Any]) -> dict[str, Any]:
    """The columns worth showing for a suspect row — enough to tell them apart."""
    return {
        "id": supplier.get("id"),
        "name": supplier.get("name"),
        "website": supplier.get("website"),
        "status": supplier.get("status"),
        "last_seen_at": supplier.get("last_seen_at"),
    }


def duplicate_supplier_groups(suppliers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the suppliers that share a normalised name or email.

    Each returned group carries the shared ``key``, the ``reason`` it matched
    (``name`` or ``email``) and the ``suppliers`` that share it. Rows with an
    empty name or email are skipped, because an absent field is not a match, and
    a supplier is only grouped once per reason however many rows agree.
    """
    suppliers = list(suppliers)
    groups: list[dict[str, Any]] = []
    for reason, key_of in (("name", normalise_name), ("email", normalise_email)):
        buckets: dict[str, list[dict[str, Any]]] = {}
        for supplier in suppliers:
            key = key_of(supplier.get(reason))
            if not key:
                continue
            buckets.setdefault(key, []).append(supplier)
        for key in sorted(buckets):
            rows = buckets[key]
            if len(rows) > 1:
                groups.append(
                    {
                        "reason": reason,
                        "key": key,
                        # Sorted by id so the report is stable however the caller
                        # ordered its rows; a check whose output moves is hard to
                        # diff between runs.
                        "suppliers": [
                            _summary(row) for row in sorted(rows, key=_by_id)
                        ],
                    }
                )
    return groups


def _by_id(supplier: dict[str, Any]) -> int:
    """Order rows by supplier id, treating a missing id as oldest."""
    return supplier.get("id") or 0
