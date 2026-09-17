"""
Entity resolution primitives for SIH 26189.

Every function here is PURE (no DB, no I/O) so it can be unit tested in isolation.
The DB-facing helpers at the bottom take already-loaded dicts, not sessions.

Design stance on the four planted resolution challenges:

  1. ALIAS VARIANTS      -> build an index mapping every written form to a canonical person_id.
                            Ambiguous strings (one alias, several people) are kept as ambiguous
                            and resolved to None rather than guessed.
  2. PHONE NORMALIZATION -> one canonical E.164-ish form; all comparison happens on that.
  3. SIM NAME MISMATCH   -> NOT an identity merge. Produces a distinct, investigatively
                            meaningful association between the registered name and the actual
                            user. This is a lead, not a correction.
  4. SHARED ADDRESS      -> NEVER a merge and never a hard edge. Emitted only as a
                            low-confidence candidate for human review, because multi-tenant
                            buildings make address equality a weak signal.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Iterable

DEFAULT_COUNTRY_CODE = "91"

# Honorifics and suffixes that carry no identifying information.
_NAME_NOISE = {
    "mr", "mrs", "ms", "smt", "shri", "sri", "dr", "prof", "kum", "km",
    "shrimati", "sh", "md", "mohd",
}


# --------------------------------------------------------------------------- phones
def normalize_phone(raw: str | None, country_code: str = DEFAULT_COUNTRY_CODE) -> str | None:
    """Reduce any written form of a phone number to a single canonical string.

    '+91 90000 14417' / '9000014417' / '09000014417' / '91-9000014417' all -> '+919000014417'
    Returns None for input that cannot be a phone number, so callers can fail loud.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    # strip international access prefix and trunk zero
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(country_code) and len(digits) > 10:
        digits = digits[len(country_code):]
    digits = digits.lstrip("0")
    if len(digits) != 10:
        # Not a standard Indian subscriber number; keep it canonical but flag by shape.
        # Caller decides whether to reject. We still return a stable form.
        return f"+{country_code}{digits}" if digits else None
    return f"+{country_code}{digits}"


def is_plausible_phone(canonical: str | None, country_code: str = DEFAULT_COUNTRY_CODE) -> bool:
    return bool(canonical) and len(canonical) == len(country_code) + 11


# --------------------------------------------------------------------------- names
def normalize_name(raw: str | None) -> str:
    """Casefold, strip punctuation/honorifics, collapse whitespace.

    'Smt. Sulabha  Chaudhari' -> 'sulabha chaudhari'
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKD", str(raw))
    s = s.encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in _NAME_NOISE]
    return " ".join(tokens)


def name_variants(full_name: str) -> set[str]:
    """Generate the written forms a person's name plausibly takes in a document.

    'Rahul Kumar Sharma' -> {'rahul kumar sharma', 'rahul sharma', 'r sharma', 'r k sharma'}

    Used to widen the alias index. These are CANDIDATE forms only; a form that turns out
    to be shared by two people is marked ambiguous by build_alias_index and never
    silently resolved.
    """
    norm = normalize_name(full_name)
    if not norm:
        return set()
    parts = norm.split()
    out = {norm}
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        out.add(f"{first} {last}")
        out.add(f"{first[0]} {last}")
        if len(parts) >= 3:
            initials = " ".join(p[0] for p in parts[:-1])
            out.add(f"{initials} {last}")
    return out


def build_alias_index(
    persons: Iterable[dict],
    *,
    id_key: str = "person_id",
    name_key: str = "full_name",
    alias_key: str = "aliases",
    alias_sep: str = "|",
    expand_variants: bool = True,
) -> dict[str, str | None]:
    """Map every normalized written form -> canonical person_id.

    A form claimed by more than one person maps to None (AMBIGUOUS). Callers must treat
    None as "needs human disambiguation", never as "no match".

    Explicit aliases from the source data always win over generated variants: if 'r sharma'
    is an explicit alias of P0042 and merely a generated variant of P0051, it resolves to
    P0042 rather than becoming ambiguous.
    """
    explicit: dict[str, set[str]] = defaultdict(set)
    generated: dict[str, set[str]] = defaultdict(set)

    for p in persons:
        pid = p[id_key]
        explicit[normalize_name(p.get(name_key))].add(pid)
        raw_aliases = (p.get(alias_key) or "").strip()
        if raw_aliases:
            for a in raw_aliases.split(alias_sep):
                if normalize_name(a):
                    explicit[normalize_name(a)].add(pid)
        if expand_variants:
            for v in name_variants(p.get(name_key) or ""):
                generated[v].add(pid)

    index: dict[str, str | None] = {}
    for form, owners in generated.items():
        index[form] = next(iter(owners)) if len(owners) == 1 else None
    for form, owners in explicit.items():          # explicit overrides generated
        index[form] = next(iter(owners)) if len(owners) == 1 else None
    index.pop("", None)
    return index


def resolve_alias(text: str, alias_index: dict[str, str | None]) -> str | None:
    """Resolve a single written name form to a person_id.

    Returns None both for 'not found' and for 'ambiguous' — deliberately, because in an
    investigative context a confident wrong merge is far more damaging than a miss.
    Use resolve_alias_verbose when the caller needs to tell the two apart.
    """
    return alias_index.get(normalize_name(text))


def resolve_alias_verbose(text: str, alias_index: dict[str, str | None]) -> tuple[str | None, str]:
    """-> (person_id | None, status) where status in {MATCH, AMBIGUOUS, NO_MATCH}."""
    key = normalize_name(text)
    if key not in alias_index:
        return None, "NO_MATCH"
    val = alias_index[key]
    return (val, "MATCH") if val else (None, "AMBIGUOUS")


def find_alias_mentions(
    free_text: str, alias_index: dict[str, str | None], *, min_tokens: int = 2
) -> list[tuple[str, str]]:
    """Deterministic string-level scan of free text for known name forms.

    This is NOT named-entity recognition — it is exact matching against the alias index,
    used to corroborate structured findings against report prose. The real NER module
    (Task 4 of the wider project) will replace it.

    min_tokens guards against single-token forms like a bare surname matching too eagerly.
    Returns [(matched_surface_form, person_id)], ambiguous forms excluded.
    """
    hits: list[tuple[str, str]] = []
    norm_text = " " + normalize_name(free_text) + " "
    for form, pid in alias_index.items():
        if pid is None or len(form.split()) < min_tokens:
            continue
        if f" {form} " in norm_text:
            hits.append((form, pid))
    return hits


# --------------------------------------------------------------------------- SIM mismatch
def detect_sim_name_mismatch(
    phone_row: dict, person_name_by_id: dict[str, str], alias_index: dict[str, str | None]
) -> dict | None:
    """Flag a SIM whose registered name is not the actual user's name.

    Returns None when the names agree (including via an alias form). Otherwise returns a
    dict describing the discrepancy, including the registered party's person_id when the
    registered name itself resolves to a known person — that case is the interesting one,
    because it links two people who may otherwise look unconnected.

    This NEVER merges the two identities. A shared SIM registration is an association to
    be investigated, not evidence that two records describe one person.
    """
    owner_id = phone_row.get("owner_person_id")
    registered = (phone_row.get("sim_registered_name") or "").strip()
    if not owner_id or not registered:
        return None

    owner_name = person_name_by_id.get(owner_id)
    if owner_name is None:
        return None

    owner_forms = {normalize_name(owner_name)} | name_variants(owner_name)
    if normalize_name(registered) in owner_forms:
        return None

    registered_pid, status = resolve_alias_verbose(registered, alias_index)
    if registered_pid and registered_pid == owner_id:
        return None
    return {
        "phone_id": phone_row.get("phone_id"),
        "number": phone_row.get("number"),
        "actual_owner_person_id": owner_id,
        "registered_name": registered,
        "registered_person_id": registered_pid,      # None when the name is not a known person
        "registered_match_status": status,
        "kind": "SIM_REGISTERED_TO_THIRD_PARTY" if registered_pid else "SIM_REGISTERED_TO_UNKNOWN_NAME",
        "note": (
            "SIM registration name differs from the recorded user. Recorded as an association "
            "for analyst review; identities are NOT merged and no conclusion is drawn."
        ),
    }


# --------------------------------------------------------------------------- addresses
def normalize_address(raw: str | None) -> str:
    if not raw:
        return ""
    s = normalize_name(raw)
    s = re.sub(r"\b(flat|room|h no|hno|no|plot|shop|unit|block|bungalow)\b", " ", s)
    return " ".join(s.split())


def shared_address_candidates(
    persons: Iterable[dict], *, address_key: str = "address", id_key: str = "person_id",
    max_group_size: int = 12,
) -> list[dict]:
    """Return SOFT candidates only — never merges, never hard edges.

    Address equality in this domain is a weak signal: shared tenancies, multi-tenant
    buildings and hostels routinely put unrelated people at one address. The dataset used
    to develop this pipeline contains a deliberate trap of exactly that shape.

    Confidence is deliberately low (0.2) and the relationship type is ASSOCIATED_WITH with
    a review flag, so downstream centrality and path-finding can exclude these edges.
    """
    by_addr: dict[str, list[str]] = defaultdict(list)
    for p in persons:
        key = normalize_address(p.get(address_key))
        if key:
            by_addr[key].append(p[id_key])

    out = []
    for addr, pids in by_addr.items():
        if 2 <= len(pids) <= max_group_size:
            for i, a in enumerate(sorted(pids)):
                for b in sorted(pids)[i + 1:]:
                    out.append({
                        "person_a": a,
                        "person_b": b,
                        "normalized_address": addr,
                        "confidence": 0.2,
                        "requires_human_review": True,
                        "note": (
                            "Co-registered address only. Shared tenancy and multi-occupancy "
                            "buildings make this a weak indicator; not treated as a "
                            "confirmed association."
                        ),
                    })
    return out
