"""
Rule-based pattern detectors for SIH 26189.

Everything here is deterministic and explainable — no ML, no LLM. Each detector takes
case_id plus its own thresholds and returns a list of Alert-shaped dicts.

LANGUAGE CONTRACT: every `reason` string describes an OBSERVATION and an ANALYTICAL
INDICATOR. None asserts wrongdoing. Severity values are review priorities
(INFORMATIONAL / REVIEW / PRIORITY_REVIEW), never guilt scores.
"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from datetime import timedelta

import networkx as nx
from sqlalchemy import delete, select

from backend.models.db import Alert, Document, Entity, Evidence, RelationshipRow
from backend.services import entity_resolution as er
from backend.services.graph_builder import load_graph

# --------------------------------------------------------------------------- helpers
def _entities(session, case_id) -> dict[int, Entity]:
    return {e.id: e for e in session.scalars(select(Entity).where(Entity.case_id == case_id))}


def _rels(session, case_id, rtype: str) -> list[RelationshipRow]:
    return list(session.scalars(select(RelationshipRow).where(
        RelationshipRow.case_id == case_id, RelationshipRow.relationship_type == rtype)))


def _evidence_ids(session, relationship_ids: list[int]) -> list[int]:
    if not relationship_ids:
        return []
    return [e.id for e in session.scalars(
        select(Evidence).where(Evidence.relationship_id.in_(relationship_ids)))]


def _owner_map(session, case_id, owned_type: str) -> dict[int, int]:
    """asset entity_id -> owning PERSON entity_id"""
    ents = _entities(session, case_id)
    out = {}
    for r in _rels(session, case_id, "OWNS"):
        if ents[r.target_entity_id].type == owned_type and ents[r.source_entity_id].type == "PERSON":
            out[r.target_entity_id] = r.source_entity_id
    return out


# ===========================================================================
# DETECTOR 1 — rapid pass-through chain
# ===========================================================================
def detect_pass_through_chains(
    session, case_id: str, *,
    window_hours: float = 48.0,
    min_hops: int = 3,
    max_hops: int = 6,
    cut_min: float = 0.003,
    cut_max: float = 0.10,
    min_amount_inr: float = 100_000.0,
    min_gap_minutes: float = 1.0,
) -> list[dict]:
    """Find funds moving through a sequence of accounts quickly, each hop a small cut down.

    Every threshold is a parameter. Defaults describe the classic layering shape (fast,
    thin margins) but are not tuned to any particular dataset.

    A match is an INDICATOR consistent with layering. It is equally consistent with
    legitimate onward settlement in a trading chain, which is precisely why the output is
    a review lead and not a determination.
    """
    ents = _entities(session, case_id)
    txns = []
    for r in _rels(session, case_id, "TRANSFERRED_TO"):
        m = r.meta
        amt = float(m.get("amount_inr") or r.weight or 0)
        if r.occurred_at is None or amt < min_amount_inr:
            continue
        txns.append({
            "rel_id": r.id, "txn_id": (r.natural_id or "").split(":", 1)[-1],
            "src": r.source_entity_id, "dst": r.target_entity_id,
            "amount": amt, "ts": r.occurred_at,
            "type": m.get("transaction_type"), "remarks": m.get("remarks"),
        })
    txns.sort(key=lambda t: t["ts"])

    out_edges: dict[int, list[dict]] = defaultdict(list)
    for t in txns:
        out_edges[t["src"]].append(t)

    chains: list[list[dict]] = []

    def extend(chain: list[dict]):
        last = chain[-1]
        start = chain[0]["ts"]
        if len(chain) >= min_hops:
            chains.append(list(chain))
        if len(chain) >= max_hops:
            return
        for nxt in out_edges.get(last["dst"], ()):
            gap = (nxt["ts"] - last["ts"]).total_seconds() / 60.0
            if gap < min_gap_minutes:
                continue
            if (nxt["ts"] - start).total_seconds() / 3600.0 > window_hours:
                continue
            if nxt["dst"] in {c["src"] for c in chain}:      # no cycling back
                continue
            cut = 1.0 - (nxt["amount"] / last["amount"])
            if not (cut_min <= cut <= cut_max):
                continue
            extend(chain + [nxt])

    for t in txns:
        extend([t])

    def is_subchain(sub, main):
        return any(main[i:i + len(sub)] == sub for i in range(len(main) - len(sub) + 1))

    # keep only maximal chains (drop a chain that is a subchain of another chain)
    keyed = {tuple(c["txn_id"] for c in ch): ch for ch in chains}
    maximal = [ch for k, ch in keyed.items()
               if not any(other != k and is_subchain(k, other) for other in keyed)]

    alerts = []
    for ch in sorted(maximal, key=lambda c: -c[0]["amount"]):
        span_h = (ch[-1]["ts"] - ch[0]["ts"]).total_seconds() / 3600.0
        accounts = [ch[0]["src"]] + [c["dst"] for c in ch]
        acct_notes = []
        for aid in accounts:
            meta = ents[aid].meta
            kyc = meta.get("kyc_status")
            if kyc and kyc.upper() not in ("VERIFIED", ""):
                acct_notes.append(f"{ents[aid].name.split(' ')[0]} KYC={kyc}")
            if meta.get("kind") == "EXTERNAL_CASH":
                acct_notes.append("chain terminates outside the banking channel (cash)")
        cuts = [round(1.0 - (b["amount"] / a["amount"]), 4) for a, b in zip(ch, ch[1:])]
        rel_ids = [c["rel_id"] for c in ch]

        alerts.append({
            "type": "RAPID_PASS_THROUGH_CHAIN",
            "severity": "PRIORITY_REVIEW" if acct_notes else "REVIEW",
            "entity_id": accounts[0],
            "reason": (
                f"Funds traversed {len(accounts)} accounts in {span_h:.1f} h "
                f"(threshold {window_hours:g} h), each hop reduced by "
                f"{min(cuts)*100:.2f}-{max(cuts)*100:.2f}% of the preceding amount. "
                f"Opening amount INR {ch[0]['amount']:,.0f}; final INR {ch[-1]['amount']:,.0f}. "
                + (f"Account attributes worth checking: {'; '.join(sorted(set(acct_notes)))}. "
                   if acct_notes else "")
                + "This movement pattern is an analytical indicator only; onward settlement in a "
                  "legitimate trading chain can look identical. Verification required."
            ),
            "details": {
                "transaction_ids": [c["txn_id"] for c in ch],
                "account_path": [ents[a].natural_id for a in accounts],
                "account_holders": [
                    ents[a].meta.get("account_holder_person_id") for a in accounts],
                "amounts": [ch[0]["amount"]] + [c["amount"] for c in ch],
                "hop_cuts_pct": [round(c * 100, 2) for c in cuts],
                "elapsed_hours": round(span_h, 2),
                "started_at": ch[0]["ts"].isoformat(),
                "kyc_flags": sorted(set(acct_notes)),
            },
            "relationship_ids": rel_ids,
            "evidence_ids": _evidence_ids(session, rel_ids),
        })
    return alerts


# ===========================================================================
# DETECTOR 2 — communication spike (PAIR level, never per-person volume)
# ===========================================================================
def detect_communication_spikes(
    session, case_id: str, *,
    window_days: int = 2,
    min_window_contacts: int = 15,
    ratio_threshold: float = 6.0,
    min_active_days: int = 4,
    directed: bool = False,
) -> list[dict]:
    """Flag phone-number PAIRS whose contact rate jumps far above their own baseline.

    Method: ratio-to-baseline, not z-score. For each pair we build a daily contact count
    series over the observed span, take a rolling `window_days` sum, and compare the peak
    window against a baseline = median daily count over that pair's ACTIVE days, excluding
    the peak window itself. A pair is flagged when the peak exceeds both an absolute floor
    and `ratio_threshold` x baseline.

    Ratio-to-own-baseline was chosen over z-score because per-pair series are short and
    mostly zero, which makes the standard deviation unstable and produces enormous
    z-scores for any pair that goes from 0 to 2 calls. The absolute floor
    (`min_window_contacts`) does the work that a variance term would do less reliably.

    DELIBERATELY NOT DONE: ranking by per-person call volume. A person with a high nightly
    call count spread across many different contacts is a shift-work pattern, not a
    coordination pattern. Detecting at the pair level makes that distinction structural
    rather than a special case — the high-volume individual never forms a high-count pair
    with any single counterpart, so nothing fires.
    """
    ents = _entities(session, case_id)
    per_pair: dict[tuple, list] = defaultdict(list)
    for r in _rels(session, case_id, "CALLS"):
        if r.meta.get("aggregate") or r.occurred_at is None:
            continue
        if ents[r.source_entity_id].type != "PHONE":
            continue
        key = ((r.source_entity_id, r.target_entity_id) if directed
               else tuple(sorted((r.source_entity_id, r.target_entity_id))))
        per_pair[key].append(r)

    phone_owner = _owner_map(session, case_id, "PHONE")
    alerts = []

    for pair, rows in per_pair.items():
        by_day = defaultdict(int)
        for r in rows:
            by_day[r.occurred_at.date()] += 1
        if len(by_day) < min_active_days:
            continue

        days = sorted(by_day)
        span = [(days[0] + timedelta(days=i)) for i in range((days[-1] - days[0]).days + 1)]
        series = [by_day.get(d, 0) for d in span]

        best_i, best_sum = 0, -1
        for i in range(len(series) - window_days + 1):
            s = sum(series[i:i + window_days])
            if s > best_sum:
                best_i, best_sum = i, s
        peak_days = span[best_i:best_i + window_days]

        baseline_vals = [by_day[d] for d in days if d not in peak_days]
        if not baseline_vals:
            continue
        baseline = statistics.median(baseline_vals)
        denom = max(baseline * window_days, 1.0)
        ratio = best_sum / denom

        if best_sum < min_window_contacts or ratio < ratio_threshold:
            continue

        a, b = pair
        owners = [phone_owner.get(a), phone_owner.get(b)]
        rel_ids = [r.id for r in rows if r.occurred_at.date() in peak_days]
        post = [d for d in days if d > peak_days[-1]]
        post_rate = (sum(by_day[d] for d in post) / len(post)) if post else None

        alerts.append({
            "type": "COMMUNICATION_SPIKE",
            "severity": "PRIORITY_REVIEW" if ratio >= ratio_threshold * 2 else "REVIEW",
            "entity_id": owners[0] or a,
            "reason": (
                f"Contact between {ents[a].name} and {ents[b].name} rose to {best_sum} "
                f"contacts over {window_days} day(s) ({peak_days[0]} to {peak_days[-1]}), "
                f"against a baseline of {baseline:g} contact(s)/day on this pair's other "
                f"active days — a {ratio:.1f}x increase. "
                + (f"Contact fell to {post_rate:.2f}/day afterwards. " if post_rate is not None else "")
                + "A short burst between two specific numbers is an analytical indicator of "
                  "coordination around a date of interest; it is not evidence of an offence "
                  "and has innocent explanations. Detected at pair level so that individuals "
                  "with high overall call volume across many contacts are not flagged."
            ),
            "details": {
                "phone_a": ents[a].name, "phone_b": ents[b].name,
                "person_a": ents[owners[0]].natural_id if owners[0] else None,
                "person_a_name": ents[owners[0]].name if owners[0] else None,
                "person_b": ents[owners[1]].natural_id if owners[1] else None,
                "person_b_name": ents[owners[1]].name if owners[1] else None,
                "peak_window": [d.isoformat() for d in peak_days],
                "peak_contacts": best_sum,
                "baseline_contacts_per_day": baseline,
                "ratio": round(ratio, 2),
                "post_window_rate_per_day": round(post_rate, 3) if post_rate is not None else None,
                "total_contacts_observed": len(rows),
            },
            "relationship_ids": rel_ids,
            "evidence_ids": _evidence_ids(session, rel_ids),
        })

    return sorted(alerts, key=lambda a: -a["details"]["ratio"])


# ===========================================================================
# DETECTOR 3 — geographic co-occurrence from independent sources
# ===========================================================================
def _site_vocabulary(ents: dict[int, Entity]) -> set[str]:
    """Area tokens taken from the LOCATION reference data itself (not hardcoded)."""
    vocab = set()
    for e in ents.values():
        if e.type == "LOCATION":
            area = e.meta.get("area")
            if area:
                vocab.update(er.normalize_name(area).split())
    return {t for t in vocab if len(t) > 3}


def _site_key(entity: Entity, vocab: set[str]) -> str:
    area = entity.meta.get("area")
    if area:
        return er.normalize_name(area)
    toks = er.normalize_name(entity.name).split()
    for t in toks:
        if t in vocab:
            return t
    return er.normalize_name(entity.name)


def detect_geographic_cooccurrence(
    session, case_id: str, *,
    window_minutes: int = 30,
    min_participants: int = 3,
    min_distinct_sources: int = 2,
    corroborate_free_text: bool = True,
) -> list[dict]:
    """Find time windows placing several distinct people at one site via different sources.

    The point is that no single source lists all the participants. A cell-site ping, an
    ANPR capture and a field report are three independent observation channels; this
    detector joins them on (site, time) and reports the union.

    `min_distinct_sources` is what makes a finding interesting: three people seen by ONE
    camera is a traffic queue, whereas the same three seen by a camera AND a tower AND a
    report is a convergence. Nearby locations are grouped into a site using area labels
    read from the location reference data itself.

    Free-text corroboration here is deterministic string matching against the alias index
    (see entity_resolution.find_alias_mentions) — it is NOT entity extraction. The NER
    module will supersede it. TODO(nlp).
    """
    ents = _entities(session, case_id)
    vocab = _site_vocabulary(ents)
    phone_owner = _owner_map(session, case_id, "PHONE")
    vehicle_owner = _owner_map(session, case_id, "VEHICLE")

    events = []
    for r in _rels(session, case_id, "PRESENT_AT"):
        if r.occurred_at is None:
            continue
        subj, loc = r.source_entity_id, r.target_entity_id
        if loc not in ents or ents[loc].type != "LOCATION":
            continue
        person = phone_owner.get(subj) or vehicle_owner.get(subj)
        if person is None:
            continue
        events.append({
            "person": person, "subject": subj, "loc": loc,
            "site": _site_key(ents[loc], vocab),
            "ts": r.occurred_at,
            "source_kind": r.meta.get("source_kind") or "UNKNOWN",
            "rel_id": r.id,
        })

    by_site = defaultdict(list)
    for ev in events:
        by_site[ev["site"]].append(ev)

    raw_windows = []
    for site, evs in by_site.items():
        evs.sort(key=lambda e: e["ts"])
        for i, anchor in enumerate(evs):
            hi = anchor["ts"] + timedelta(minutes=window_minutes)
            bucket = [e for e in evs[i:] if e["ts"] <= hi]
            people = {e["person"] for e in bucket}
            sources = {e["source_kind"] for e in bucket}
            if len(people) >= min_participants and len(sources) >= min_distinct_sources:
                raw_windows.append({
                    "site": site, "start": anchor["ts"], "end": max(e["ts"] for e in bucket),
                    "people": people, "sources": sources, "events": bucket,
                })

    # de-duplicate overlapping windows: combine participants and sources across overlapping windows
    raw_windows.sort(key=lambda w: (w["site"], w["start"]))
    merged = []
    for w in raw_windows:
        if merged and merged[-1]["site"] == w["site"] and \
                w["start"] <= merged[-1]["end"] + timedelta(minutes=window_minutes):
            prev = merged[-1]
            prev["people"].update(w["people"])
            prev["sources"].update(w["sources"])
            existing_event_ids = {id(e) for e in prev["events"]}
            for e in w["events"]:
                if id(e) not in existing_event_ids:
                    prev["events"].append(e)
            prev["end"] = max(prev["end"], w["end"])
        else:
            merged.append(dict(w))

    # optional free-text corroboration
    reports_by_date = {}
    if corroborate_free_text:
        alias_index = _alias_index_from_db(session, case_id)
        for doc in session.scalars(select(Document).where(
                Document.case_id == case_id,
                Document.document_type.in_(("SURVEILLANCE", "INTEL")))):
            if not doc.text or doc.id.endswith(".txt"):
                continue
            dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", doc.text) + \
                    [f"2026-{m}-{d}" for d, m in re.findall(r"\b(\d{2})/(\d{2})/2026\b", doc.text)]
            mentions = er.find_alias_mentions(doc.text, alias_index)
            for d in set(dates):
                reports_by_date.setdefault(d, []).append(
                    {"document_id": doc.id, "person_ids": sorted({p for _, p in mentions}),
                     "text": doc.text})

    alerts = []
    for w in merged:
        rel_ids = [e["rel_id"] for e in w["events"]]
        per_person_src = defaultdict(set)
        for e in w["events"]:
            per_person_src[e["person"]].add(e["source_kind"])
        site_locations = sorted({ents[e["loc"]].name for e in w["events"]})

        corroboration = []
        for d, entries in reports_by_date.items():
            if d != w["start"].date().isoformat():
                continue
            for entry in entries:
                overlap = [p for p in entry["person_ids"]
                           if any(ents[pid].natural_id == f"PERSON:{p}" for pid in w["people"])]
                site_tokens = set(w["site"].split())
                if overlap or site_tokens & set(er.normalize_name(entry["text"]).split()):
                    corroboration.append({"document_id": entry["document_id"],
                                          "persons_named": overlap})

        names = sorted(ents[p].name for p in w["people"])
        alerts.append({
            "type": "GEOGRAPHIC_CO_OCCURRENCE",
            "severity": "PRIORITY_REVIEW" if len(w["sources"]) >= 2 and len(w["people"]) >= 3
                        else "REVIEW",
            "entity_id": sorted(w["people"])[0],
            "reason": (
                f"{len(w['people'])} subjects are placed at {w['site'].title()} between "
                f"{w['start']:%Y-%m-%d %H:%M} and {w['end']:%H:%M} by "
                f"{len(w['sources'])} independent record types "
                f"({', '.join(sorted(w['sources']))}). Subjects: {', '.join(names)}. "
                f"No single source lists all of them together"
                + (f"; field reporting for the same date corroborates "
                   f"({', '.join(c['document_id'] for c in corroboration)})." if corroboration else ".")
                + " Co-location is an analytical indicator of possible contact; it does not "
                  "establish that a meeting occurred or that its purpose was unlawful."
            ),
            "details": {
                "site": w["site"], "locations": site_locations,
                "window_start": w["start"].isoformat(), "window_end": w["end"].isoformat(),
                "participants": [{"person": ents[p].natural_id, "name": ents[p].name,
                                  "placed_by": sorted(per_person_src[p])}
                                 for p in sorted(w["people"], key=lambda x: ents[x].name)],
                "source_kinds": sorted(w["sources"]),
                "free_text_corroboration": corroboration,
            },
            "relationship_ids": rel_ids,
            "evidence_ids": _evidence_ids(session, rel_ids),
        })

    return sorted(alerts, key=lambda a: (-len(a["details"]["participants"]),
                                         a["details"]["window_start"]))


def _alias_index_from_db(session, case_id: str) -> dict:
    persons = [{"person_id": e.natural_id.split(":", 1)[1], "full_name": e.name,
                "aliases": "|".join(e.meta.get("aliases") or [])}
               for e in session.scalars(select(Entity).where(
                   Entity.case_id == case_id, Entity.type == "PERSON"))]
    return er.build_alias_index(persons)


# ===========================================================================
# DETECTOR 4 — hidden multi-hop path across the heterogeneous graph
# ===========================================================================
def find_paths(
    session, case_id: str, source_natural_id: str, target_natural_id: str, *,
    max_hops: int = 6, max_paths: int = 5, include_weak_edges: bool = False,
    min_edge_support: int = 1, min_confidence: float = 0.0,
    exclude_relationship_types: set[str] | None = None,
    transit_blocked_types: set[str] | None = ("LOCATION",),
    graph: nx.MultiDiGraph | None = None,
) -> list[dict]:
    """Shortest paths between any two entities, across ALL relationship types.

    Traversal is undirected: a payment from A to B connects them for discovery purposes
    even though the money moved one way. Direction and type are preserved in the reported
    hops so the analyst sees exactly what each link is.

    `include_weak_edges=False` (default) drops review-only edges such as shared-address
    candidates. That default matters: with weak edges enabled, unrelated co-tenants become
    one hop apart and the path finder will happily report a short, meaningless connection.

    `min_edge_support` is the second guard, and it matters just as much. A shortest-path
    search treats every edge as equally real, so ONE incidental phone call between two
    people creates a one-hop link that outranks — and hides — a substantive multi-hop
    chain. Raising min_edge_support requires an aggregated CALLS edge to rest on at least
    that many underlying records before it may be traversed. This is a statement about
    evidentiary weight, not about the parties: a single call is a weak basis for asserting
    a relationship, whichever two people made it.

    `transit_blocked_types` is the third guard, and without it the whole feature is
    worthless. LOCATION nodes are hubs by nature — hundreds of subjects touch one cell
    tower over six weeks — so a path finder allowed to route THROUGH a location will
    connect any two people in two hops via "both were somewhere near this mast at some
    point in six weeks", with no requirement that they were there at the same time. Those
    paths are formally valid and analytically worthless. Locations therefore remain
    reachable as endpoints but cannot be used as stepping stones; establishing that two
    subjects were genuinely co-located is the job of detect_geographic_cooccurrence, which
    applies the time constraint this traversal cannot.
    """
    g = graph if graph is not None else load_graph(session, case_id)
    ents = _entities(session, case_id)
    by_nid = {e.natural_id: e.id for e in ents.values()}
    if source_natural_id not in by_nid or target_natural_id not in by_nid:
        missing = source_natural_id if source_natural_id not in by_nid else target_natural_id
        raise KeyError(f"entity {missing!r} not found in case {case_id}")
    s, t = by_nid[source_natural_id], by_nid[target_natural_id]

    u = nx.Graph()
    u.add_nodes_from(g.nodes(data=True))
    for a, b, d in g.edges(data=True):
        if d.get("excluded_from_metrics") and not include_weak_edges:
            continue
        if float(d.get("confidence") or 1.0) < min_confidence:
            continue
        if exclude_relationship_types and d["relationship_type"] in exclude_relationship_types:
            continue
        meta = d.get("meta") or {}
        if min_edge_support > 1 and meta.get("aggregate"):
            if int(meta.get("call_count") or 0) < min_edge_support:
                continue
        edge_conf = float(d.get("confidence") or 1.0)
        if not u.has_edge(a, b) or edge_conf > u[a][b].get("confidence", 0):
            u.add_edge(a, b, confidence=edge_conf)

    blocked = set(transit_blocked_types or ())
    if blocked:
        nodes_to_remove = [n for n in u.nodes if n != s and n != t and ents[n].type in blocked]
        u.remove_nodes_from(nodes_to_remove)

    results = []
    try:
        for path in nx.shortest_simple_paths(u, s, t):
            if len(path) - 1 > max_hops:
                break
            results.append(_describe_path(session, g, ents, path))
            if len(results) >= max_paths:
                break
    except nx.NetworkXNoPath:
        return []
    return results


def _describe_path(session, g, ents, path) -> dict:
    hops, rel_ids, min_conf = [], [], 1.0
    for a, b in zip(path, path[1:]):
        cands = []
        for u_, v_, d in list(g.edges(a, data=True)) + list(g.in_edges(a, data=True)):
            if {u_, v_} == {a, b}:
                cands.append((u_, v_, d))
        best = max(cands, key=lambda c: c[2].get("confidence", 0))
        u_, v_, d = best
        rel_ids.append(d["relationship_id"])
        min_conf = min(min_conf, float(d.get("confidence") or 1.0))
        ev = session.scalars(select(Evidence).where(
            Evidence.relationship_id == d["relationship_id"])).first()
        hops.append({
            "from": ents[a].natural_id, "from_name": ents[a].name, "from_type": ents[a].type,
            "to": ents[b].natural_id, "to_name": ents[b].name, "to_type": ents[b].type,
            "relationship_type": d["relationship_type"],
            "direction": "forward" if u_ == a else "reverse",
            "confidence": d.get("confidence"),
            "occurred_at": d["occurred_at"].isoformat() if d.get("occurred_at") else None,
            "evidence": {"document_id": ev.document_id, "page": ev.page,
                         "snippet": ev.text_snippet} if ev else None,
        })
    docs = {h["evidence"]["document_id"] for h in hops if h["evidence"]}
    return {
        "type": "MULTI_HOP_CONNECTION",
        "severity": "REVIEW",
        "hop_count": len(hops),
        "endpoints": [ents[path[0]].natural_id, ents[path[-1]].natural_id],
        "endpoint_names": [ents[path[0]].name, ents[path[-1]].name],
        "path_confidence": round(min_conf, 3),
        "distinct_source_documents": sorted(docs),
        "hops": hops,
        "reason": (
            f"A {len(hops)}-hop connection links {ents[path[0]].name} to {ents[path[-1]].name} "
            f"via {' -> '.join(h['relationship_type'] for h in hops)}, drawing on "
            f"{len(docs)} distinct source documents. No single document states this "
            f"connection. Weakest link confidence {min_conf:.2f}. An indirect connection is "
            f"a starting point for enquiry, not an indication that either party is "
            f"implicated in the other's activity."
        ),
        "relationship_ids": rel_ids,
        "evidence_ids": _evidence_ids(session, rel_ids),
    }


def discover_bridging_paths(
    session, case_id: str, *, max_hops: int = 8, min_hops: int = 3,
    min_edge_support: int = 2, min_source_documents: int = 2,
    max_paths_per_pair: int = 3,
) -> list[dict]:
    """Surface non-obvious connections without being told which pair to look at.

    Takes the highest-degree PERSON in each detected community and asks for a path to the
    equivalent in every other community, keeping only paths of at least `min_hops` that
    cross more than one source document — i.e. connections that could not be read off a
    single file.
    """
    from backend.services.graph_builder import person_projection
    h = person_projection(session, case_id)
    ents = _entities(session, case_id)
    if h.number_of_nodes() == 0:
        return []
    try:
        comms = list(nx.community.louvain_communities(h, weight="weight", seed=42))
    except AttributeError:
        comms = list(nx.community.greedy_modularity_communities(h, weight="weight"))

    reps = []
    for c in comms:
        if len(c) < 3:
            continue
        reps.append(max(c, key=lambda n: h.degree(n, weight="weight")))

    g = load_graph(session, case_id)
    seen, out = set(), []
    for i, a in enumerate(reps):
        for b in reps[i + 1:]:
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            seen.add(key)
            for p in find_paths(session, case_id, ents[a].natural_id, ents[b].natural_id,
                                max_hops=max_hops, max_paths=max_paths_per_pair,
                                min_edge_support=min_edge_support, graph=g):
                if (p["hop_count"] >= min_hops
                        and len(p["distinct_source_documents"]) >= min_source_documents):
                    out.append(p)
                    break
    return sorted(out, key=lambda p: (-len(p["distinct_source_documents"]), p["hop_count"]))


# ===========================================================================
# persistence + driver
# ===========================================================================
def rank_by_corroboration(session, case_id: str, detector_output: dict,
                          *, betweenness: dict[int, float] | None = None) -> list[dict]:
    """Rank subjects by how many INDEPENDENT detector families name them.

    Rationale, and the honest reason this function exists: on realistic data no single
    centrality metric separates a broker from a merely sociable person. Someone who calls
    fifteen colleagues nightly is genuinely central in the graph — betweenness is measuring
    something true about them, just not something investigative. Structure alone cannot
    make that distinction, and tuning thresholds until the "right" name surfaces is
    overfitting to a known answer, not analysis.

    What does distinguish them is corroboration across observation types that are
    independent of one another. A person appearing in a financial-layering indicator AND a
    pair-level communication burst AND a multi-source co-location window has been surfaced
    by three separate evidence channels; a person appearing in one is a single-channel
    artefact. Ranking on that intersection is defensible without reference to any answer
    key, because it does not depend on which subjects those detectors happened to name.

    Still an ordering of REVIEW PRIORITY, not of suspicion, and a subject may appear in
    several indicators for entirely lawful reasons.
    """
    ents = _entities(session, case_id)
    hits: dict[int, set[str]] = defaultdict(set)
    detail: dict[int, list[str]] = defaultdict(list)

    def note(pid, fam, txt):
        if pid in ents:
            hits[pid].add(fam)
            detail[pid].append(txt)

    by_nid = {e.natural_id: e.id for e in ents.values()}
    holder_person = {}
    for r in _rels(session, case_id, "OWNS"):
        if ents[r.target_entity_id].type == "BANK_ACCOUNT":
            holder_person[ents[r.target_entity_id].natural_id] = r.source_entity_id

    for a in detector_output.get("pass_through_chains", []):
        for acc in a["details"]["account_path"]:
            note(holder_person.get(acc), "PASS_THROUGH", f"account {acc} in chain "
                 f"{'->'.join(a['details']['transaction_ids'])}")
    for a in detector_output.get("communication_spikes", []):
        for k in ("person_a", "person_b"):
            note(by_nid.get(a["details"][k]), "COMM_SPIKE",
                 f"contact burst {a['details']['peak_window'][0]}")
    for a in detector_output.get("geographic_cooccurrence", []):
        for p in a["details"]["participants"]:
            note(by_nid.get(p["person"]), "CO_LOCATION",
                 f"{a['details']['site']} {a['details']['window_start'][:16]}")
    for p in detector_output.get("bridging_paths", []):
        for h in p["hops"]:
            for side in ("from", "to"):
                eid = by_nid.get(h[side])
                if eid and ents[eid].type == "PERSON":
                    note(eid, "MULTI_HOP", f"on path {p['endpoints'][0]}..{p['endpoints'][1]}")

    out = []
    for eid, fams in hits.items():
        if ents[eid].type != "PERSON":
            continue
        out.append({
            "entity_id": eid, "natural_id": ents[eid].natural_id, "name": ents[eid].name,
            "detector_families": sorted(fams), "family_count": len(fams),
            "betweenness": round((betweenness or {}).get(eid, 0.0), 5),
            "supporting_observations": sorted(set(detail[eid]))[:6],
            "note": ("Appears in multiple independent indicator types. This is a review "
                     "priority, not a finding; lawful explanations may account for all of it."),
        })
    return sorted(out, key=lambda r: (-r["family_count"], -r["betweenness"]))


def persist_alerts(session, case_id: str, alerts: list[dict]):
    session.execute(delete(Alert).where(Alert.case_id == case_id))
    for a in alerts:
        session.add(Alert(case_id=case_id, entity_id=a.get("entity_id"), type=a["type"],
                          severity=a.get("severity", "REVIEW"), reason=a["reason"],
                          evidence_ids=json.dumps(a.get("evidence_ids", []))))
    session.commit()


def run_all_detectors(session, case_id: str, **overrides) -> dict:
    return {
        "pass_through_chains": detect_pass_through_chains(
            session, case_id, **overrides.get("pass_through", {})),
        "communication_spikes": detect_communication_spikes(
            session, case_id, **overrides.get("comm_spike", {})),
        "geographic_cooccurrence": detect_geographic_cooccurrence(
            session, case_id, **overrides.get("geo", {})),
        "bridging_paths": discover_bridging_paths(
            session, case_id, **overrides.get("paths", {})),
    }
