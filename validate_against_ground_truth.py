"""Compare pipeline output against ground_truth_answer_key.md.

The answer key is used ONLY here, for validation. No ID from it appears in pipeline logic.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.models.db import init_db
from backend.services import pattern_detector as pd
from backend.services.network_analysis import run_full_analysis

CASE = "CASE-2026-DEMO"
S = init_db("sih26189.db")

# --- what the key says was planted (transcribed here for comparison only) ---
GT = {
    "chain": ["TXN-0133", "TXN-0136", "TXN-0141", "TXN-0142"],
    "chain_echo": ["TXN-0180", "TXN-0182", "TXN-0186"],
    "spike_pair": {"P0201", "P0204"},
    "spike_days": {"2026-03-17", "2026-03-18"},
    "spike_count": 59,
    "geo_participants": {"P0201", "P0202", "P0203", "P0207"},
    "geo_date": "2026-03-19",
    "bridge": "P0201",
    "red_herrings": {"P0301", "P0302", "P0303"},
    "hidden_path_endpoints": ("PERSON:P0101", "PERSON:P0206"),
}

report = []
def line(s=""): report.append(s)

with S() as s:
    ents = {e.natural_id: e for e in pd._entities(s, CASE).values()}
    nid = {e.id: e.natural_id for e in pd._entities(s, CASE).values()}

    line("### 1. RAPID PASS-THROUGH CHAIN")
    default = pd.detect_pass_through_chains(s, CASE)
    got = [d["details"]["transaction_ids"] for d in default]
    line(f"default params (cut_min=0.005): {got}")
    hit = any(set(GT['chain'][:3]) <= set(g) for g in got)
    line(f"  main chain first 3 legs found: {hit}")
    line(f"  echo chain found: {any(set(GT['chain_echo']) <= set(g) for g in got)}")
    line(f"  cash-out leg TXN-0142 included: {any('TXN-0142' in g for g in got)}")
    relaxed = pd.detect_pass_through_chains(s, CASE, cut_min=0.003)
    got2 = [d["details"]["transaction_ids"] for d in relaxed]
    line(f"cut_min=0.003: {got2}")
    line(f"  cash-out leg now included: {any('TXN-0142' in g for g in got2)}")
    line(f"  false positives beyond the two planted chains: "
         f"{len([g for g in got2 if not (set(GT['chain'][:3])<=set(g) or set(GT['chain_echo'])<=set(g))])}")
    line()

    line("### 2. COMMUNICATION SPIKE")
    spikes = pd.detect_communication_spikes(s, CASE)
    line(f"leads returned: {len(spikes)}")
    for a in spikes:
        d = a["details"]
        pa = (d["person_a"] or "").split(":")[-1]; pb = (d["person_b"] or "").split(":")[-1]
        line(f"  {pa}/{pb} peak={d['peak_contacts']} days={d['peak_window']} ratio={d['ratio']}")
        line(f"    matches planted pair: {{{pa},{pb}}} == {GT['spike_pair']} -> {set([pa,pb])==GT['spike_pair']}")
        line(f"    matches planted dates: {set(d['peak_window'])==GT['spike_days']}")
        line(f"    matches planted count: {d['peak_contacts']==GT['spike_count']}")
    rh_flagged = [a for a in spikes if {(a['details']['person_a'] or '').split(':')[-1],
                                        (a['details']['person_b'] or '').split(':')[-1]} & GT["red_herrings"]]
    line(f"  red-herring persons appearing in any spike lead: {len(rh_flagged)} (want 0)")
    line()

    line("### 3. GEOGRAPHIC CO-OCCURRENCE")
    geo = pd.detect_geographic_cooccurrence(s, CASE)
    line(f"leads returned: {len(geo)}")
    for a in geo:
        d = a["details"]
        parts = {p["person"].split(":")[-1] for p in d["participants"]}
        planted = d["window_start"][:10] == GT["geo_date"]
        line(f"  {d['site']} {d['window_start'][:16]} participants={sorted(parts)} "
             f"sources={d['source_kinds']} corrob={[c['document_id'] for c in d['free_text_corroboration']]}")
        if planted:
            line(f"    PLANTED EVENT: recovered {sorted(parts & GT['geo_participants'])}, "
                 f"missed {sorted(GT['geo_participants'] - parts)}, extra {sorted(parts - GT['geo_participants'])}")
    line()

    line("### 4. HIDDEN MULTI-HOP PATH")
    for label, kw in [
        ("defaults", {}),
        ("min_edge_support=2", {"min_edge_support": 2}),
        ("non-telephonic, weak edges on, 10 hops",
         {"min_edge_support": 2, "max_hops": 10, "include_weak_edges": True,
          "exclude_relationship_types": {"CALLS"}}),
    ]:
        ps = pd.find_paths(s, CASE, *GT["hidden_path_endpoints"], max_paths=1, **kw)
        if ps:
            p = ps[0]
            line(f"  [{label}] {p['hop_count']} hops via "
                 f"{' -> '.join(h['relationship_type'] for h in p['hops'])} "
                 f"| docs {p['distinct_source_documents']}")
        else:
            line(f"  [{label}] no path")
    line()

    line("### 5. BRIDGE ENTITY / CENTRALITY")
    a = run_full_analysis(s, CASE, persist_results=False)
    for view in ("raw", "person"):
        top = a["views"][view]["top"]["betweenness_centrality"]
        line(f"  {view} view top-5 betweenness:")
        for i, r in enumerate(top, 1):
            tag = ""
            pid = r["natural_id"].split(":")[-1]
            if pid == GT["bridge"]: tag = "  <-- planted bridge"
            if pid in GT["red_herrings"]: tag = "  <-- RED HERRING (should not be top-ranked)"
            line(f"    {i}. {r['value']:.5f} {r['name']} [{r['natural_id']}]{tag}")
    line()

print("\n".join(report))
