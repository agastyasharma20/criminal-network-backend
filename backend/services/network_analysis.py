"""
Structural network metrics, persisted to the Analysis table.

Every metric here is a descriptive property of the graph, not a statement about a person.
A high betweenness score means "many shortest paths in the recorded data run through this
node" — which may indicate a broker, or may indicate an ordinary logistics contractor, or
may indicate a data artefact. Downstream text must present these as analytical indicators
for human review.

Metrics are computed on TWO views:
  * "raw"    — the full simple projection (all entity types)
  * "person" — the person-only projection, where asset-mediated links are folded onto
               their owners. This is the view an investigator actually means when they ask
               "who is central?", because raw-graph centrality mostly measures how many
               phones and accounts someone holds.
"""
from __future__ import annotations

from datetime import datetime, timezone

import networkx as nx
from sqlalchemy import delete, select

from backend.models.db import Analysis, Entity
from backend.services.graph_builder import load_graph, person_projection, project_simple

METRICS = ("degree_centrality", "betweenness_centrality", "pagerank", "community_id")


def compute_metrics(h: nx.Graph, *, weight: str | None = "weight") -> dict[str, dict]:
    if h.number_of_nodes() == 0:
        return {m: {} for m in METRICS}

    degree = nx.degree_centrality(h)
    betweenness = nx.betweenness_centrality(h, weight=None, normalized=True)
    try:
        pagerank = nx.pagerank(h, weight=weight)
    except nx.PowerIterationFailedConvergence:
        pagerank = nx.pagerank(h, weight=weight, max_iter=500, tol=1e-4)

    communities = _detect_communities(h)
    community_id = {n: i for i, comm in enumerate(communities) for n in comm}

    return {"degree_centrality": degree, "betweenness_centrality": betweenness,
            "pagerank": pagerank, "community_id": community_id,
            "_communities": communities}


def _detect_communities(h: nx.Graph) -> list[set]:
    """Louvain where available (networkx >= 3.0), greedy modularity as fallback."""
    try:
        return list(nx.community.louvain_communities(h, weight="weight", seed=42))
    except (AttributeError, ImportError):
        return list(nx.community.greedy_modularity_communities(h, weight="weight"))


def _betweenness_note() -> str:
    return ("Betweenness is computed on unweighted shortest paths: edge 'weight' in this "
            "graph means strength of association, not traversal cost, so passing it to a "
            "shortest-path metric would invert its meaning.")


def persist(session, case_id: str, view: str, metrics: dict[str, dict]):
    names = [m for m in METRICS]
    session.execute(
        delete(Analysis).where(Analysis.case_id == case_id,
                               Analysis.metric.in_([f"{view}.{m}" for m in names])))
    now = datetime.now(timezone.utc)
    for m in names:
        for node_id, value in metrics.get(m, {}).items():
            session.add(Analysis(case_id=case_id, entity_id=node_id, metric=f"{view}.{m}",
                                 value=float(value), computed_at=now))
    session.flush()


def run_full_analysis(session, case_id: str, *, top_n: int = 5, persist_results: bool = True) -> dict:
    """Compute all metrics on both views, persist them, and return a summary.

    Returns top-N lists per metric so the pipeline can be sanity-checked without querying
    the DB by hand.
    """
    g = load_graph(session, case_id)
    views = {
        "raw": project_simple(g),
        "person": person_projection(session, case_id, g),
        # Same person view, but only person-pairs attested by >= 2 underlying records.
        # See person_projection's docstring for why this changes the ranking materially.
        "person_supported": person_projection(session, case_id, g, min_edge_support=2),
    }

    name_of = {e.id: (e.name, e.type, e.natural_id) for e in
               session.scalars(select(Entity).where(Entity.case_id == case_id))}

    out = {"case_id": case_id,
           "graph": {"multidigraph_nodes": g.number_of_nodes(),
                     "multidigraph_edges": g.number_of_edges()},
           "notes": [_betweenness_note(),
                     "Metrics describe graph structure only. They are not risk scores and "
                     "carry no implication of wrongdoing."],
           "views": {}}

    for view, h in views.items():
        metrics = compute_metrics(h)
        if persist_results:
            persist(session, case_id, view, metrics)

        comms = metrics.get("_communities", [])
        summary = {"nodes": h.number_of_nodes(), "edges": h.number_of_edges(),
                   "communities": len(comms),
                   "community_sizes": sorted((len(c) for c in comms), reverse=True)[:10],
                   "top": {}}
        for m in ("degree_centrality", "betweenness_centrality", "pagerank"):
            ranked = sorted(metrics[m].items(), key=lambda kv: kv[1], reverse=True)[:top_n]
            summary["top"][m] = [
                {"entity_id": nid, "natural_id": name_of.get(nid, ("", "", ""))[2],
                 "name": name_of.get(nid, ("?",))[0], "type": name_of.get(nid, ("", "?"))[1],
                 "value": round(val, 6)}
                for nid, val in ranked]
        out["views"][view] = summary

    if persist_results:
        session.commit()
    return out


def print_analysis(summary: dict):
    print("=" * 72)
    print(f"NETWORK ANALYSIS — case {summary['case_id']}")
    print("=" * 72)
    print(f"MultiDiGraph: {summary['graph']['multidigraph_nodes']} nodes, "
          f"{summary['graph']['multidigraph_edges']} edges")
    for view, v in summary["views"].items():
        print(f"\n--- view: {view} ({v['nodes']} nodes / {v['edges']} edges, "
              f"{v['communities']} communities, sizes {v['community_sizes']}) ---")
        for metric, rows in v["top"].items():
            print(f"  {metric}")
            for i, r in enumerate(rows, 1):
                print(f"    {i}. {r['value']:<10.6f} {r['type']:<12} {r['name']}  [{r['natural_id']}]")
    print("\n" + "\n".join(f"note: {n}" for n in summary["notes"]))
    print("=" * 72)


if __name__ == "__main__":
    import argparse
    from backend.models.db import init_db

    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--db", default="sih26189.db")
    a = ap.parse_args()
    Session = init_db(a.db)
    with Session() as s:
        print_analysis(run_full_analysis(s, a.case_id))
