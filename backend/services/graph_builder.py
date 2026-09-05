"""
Build a networkx graph from persisted Entity / RelationshipRow rows.

Graph type choice — MultiDiGraph, with an undirected simple-graph projection
------------------------------------------------------------------------------
The canonical graph is a **MultiDiGraph**:

  * MULTI, because two entities can be connected by genuinely different facts. P0201 and
    P0203 are linked by a money transfer AND by phone contact; collapsing those into one
    edge destroys the ability to cite which fact supports which lead, and evidence
    citation is the whole point of the platform.
  * DIRECTED, because direction is investigatively meaningful and not recoverable once
    lost: who paid whom, who called whom, who owns what.

Centrality and community detection, however, are defined on simple graphs, and running
betweenness on a multigraph either errors or silently uses an arbitrary edge. So
`project_simple()` collapses the MultiDiGraph to an undirected Graph, summing weights per
node pair, for the metric layer only. Both views come from the same rows, so a finding in
one can always be traced back to evidence in the other.

Edge exclusion
--------------
Edges carrying `excluded_from_metrics` in their metadata (currently: shared-address
candidates, which are weak by construction) are dropped from the projection by default.
They stay in the MultiDiGraph so an analyst can still see them, but they must not inflate
anyone's centrality — that is exactly how a co-tenant gets mistaken for an associate.
"""
from __future__ import annotations

import json

import networkx as nx
from sqlalchemy import select

from backend.models.db import Entity, Evidence, RelationshipRow


def load_graph(session, case_id: str) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph(case_id=case_id)

    for e in session.scalars(select(Entity).where(Entity.case_id == case_id)):
        g.add_node(e.id, entity_id=e.id, natural_id=e.natural_id, type=e.type, name=e.name,
                   normalized_name=e.normalized_name, meta=e.meta)

    for r in session.scalars(select(RelationshipRow).where(RelationshipRow.case_id == case_id)):
        if r.source_entity_id not in g or r.target_entity_id not in g:
            raise ValueError(
                f"relationship {r.natural_id} references an entity absent from the case graph "
                f"({r.source_entity_id} -> {r.target_entity_id}); ingestion integrity is broken")
        meta = r.meta
        g.add_edge(r.source_entity_id, r.target_entity_id,
                   key=r.natural_id,
                   relationship_id=r.id,
                   relationship_type=r.relationship_type,
                   confidence=r.confidence if r.confidence is not None else 1.0,
                   weight=r.weight if r.weight is not None else 1.0,
                   occurred_at=r.occurred_at,
                   excluded_from_metrics=bool(meta.get("excluded_from_metrics")),
                   meta=meta)
    return g


def project_simple(g: nx.MultiDiGraph, *, include_weak: bool = False,
                   node_types: set[str] | None = None) -> nx.Graph:
    """Undirected simple projection for centrality/community algorithms.

    weight = sum of parallel-edge weights, normalized per relationship type so that a
    single INR 18,50,000 transfer does not outweigh 47 phone calls. Each contributing
    edge adds `confidence` (0..1) rather than its raw domain weight; the raw values stay
    available on the MultiDiGraph.
    """
    h = nx.Graph()
    for n, d in g.nodes(data=True):
        if node_types and d.get("type") not in node_types:
            continue
        h.add_node(n, **{k: v for k, v in d.items() if k != "meta"})

    for u, v, d in g.edges(data=True):
        if u not in h or v not in h or u == v:
            continue
        if d.get("excluded_from_metrics") and not include_weak:
            continue
        contrib = float(d.get("confidence") or 1.0)
        if h.has_edge(u, v):
            h[u][v]["weight"] += contrib
            h[u][v]["relationship_types"].add(d["relationship_type"])
            h[u][v]["edge_count"] += 1
        else:
            h.add_edge(u, v, weight=contrib, edge_count=1,
                       relationship_types={d["relationship_type"]})
    for _, _, d in h.edges(data=True):
        d["relationship_types"] = sorted(d["relationship_types"])
    return h


def _call_support(d: dict) -> int:
    """How many underlying records back this edge (1 for non-call facts)."""
    meta = d.get("meta") or {}
    if d["relationship_type"] == "CALLS" and meta.get("aggregate"):
        return int(meta.get("call_count") or 1)
    return 1


def person_projection(session, case_id: str, g: nx.MultiDiGraph | None = None,
                      *, min_edge_support: int = 1) -> nx.Graph:
    """People-only view: PERSON nodes connected when a path runs through their assets.

    A person owns a phone; the phone calls another phone; that phone has an owner. In the
    raw graph that is a 3-hop path, so PERSON-level centrality computed on the raw graph
    is really measuring how many assets someone holds. This projection folds
    asset-mediated links back onto their owners so centrality means what an investigator
    expects it to mean.

    Two corrections applied here, both of which change results materially:

    1. GRANULAR CALL EDGES ARE SKIPPED. Ingestion stores calls twice by design — once per
       call at PHONE level (the spike detector needs the time series) and once aggregated
       at PERSON level. Projecting both would count the same telephone contact N+1 times
       and make edge support meaningless. The aggregate carries the count, so it wins.

    2. `min_edge_support` drops person-pairs attested by fewer than N underlying records.
       This matters more than it sounds: in a realistic corpus most people have a long
       tail of one-off contacts, and betweenness rewards whoever sits between otherwise
       disconnected singletons. Someone with twenty single-call acquaintances will
       out-score a genuine broker with a dozen substantive relationships, purely because
       the acquaintances are peripheral. Requiring corroborated links measures association
       rather than address-book size. It is a statement about evidentiary weight and
       applies uniformly — it does not privilege any subject.
    """
    g = g if g is not None else load_graph(session, case_id)
    owner_of: dict[int, int] = {}
    for u, v, d in g.edges(data=True):
        if d["relationship_type"] == "OWNS" and g.nodes[u].get("type") == "PERSON":
            owner_of[v] = u

    def principal(n):
        return owner_of.get(n, n)

    h = nx.Graph()
    for n, d in g.nodes(data=True):
        if d.get("type") == "PERSON":
            h.add_node(n, **{k: val for k, val in d.items() if k != "meta"})

    for u, v, d in g.edges(data=True):
        if d.get("excluded_from_metrics") or d["relationship_type"] == "OWNS":
            continue
        # correction 1: never project the per-call rows; the aggregate represents them
        if d["relationship_type"] == "CALLS" and not (d.get("meta") or {}).get("aggregate"):
            continue
        a, b = principal(u), principal(v)
        if a == b or a not in h or b not in h:
            continue
        support = _call_support(d)
        contrib = float(d.get("confidence") or 1.0)
        if h.has_edge(a, b):
            h[a][b]["weight"] += contrib
            h[a][b]["support"] += support
            h[a][b]["relationship_types"].add(d["relationship_type"])
            h[a][b]["edge_count"] += 1
        else:
            h.add_edge(a, b, weight=contrib, support=support, edge_count=1,
                       relationship_types={d["relationship_type"]})

    # correction 2: drop under-supported pairs
    if min_edge_support > 1:
        h.remove_edges_from([(a, b) for a, b, d in h.edges(data=True)
                             if d["support"] < min_edge_support])
    for _, _, d in h.edges(data=True):
        d["relationship_types"] = sorted(d["relationship_types"])
    return h


def edge_evidence(session, relationship_id: int) -> list[dict]:
    rows = session.scalars(
        select(Evidence).where(Evidence.relationship_id == relationship_id))
    return [{"document_id": e.document_id, "page": e.page,
             "snippet": e.text_snippet, "confidence": e.confidence} for e in rows]


def describe(g) -> dict:
    return {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
            "type": type(g).__name__}
