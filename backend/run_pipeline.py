"""
End-to-end driver: ingest -> resolve -> graph -> analyse -> detect.

    python -m backend.run_pipeline --case-id CASE-2026-DEMO --dataset-dir ./dataset --reset

Everything it prints is an investigative LEAD or a structural measurement. Nothing here
asserts that any subject has committed an offence.
"""
from __future__ import annotations

import argparse
import os
import sys

from backend.models.db import init_db
from backend.services import pattern_detector as pdet
from backend.services.ingest import Ingestor, print_summary
from backend.services.network_analysis import print_analysis, run_full_analysis


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--db", default="sih26189.db")
    ap.add_argument("--reset", action="store_true", help="delete the sqlite file first")
    ap.add_argument("--min-edge-support", type=int, default=2)
    args = ap.parse_args(argv)

    if args.reset and os.path.exists(args.db):
        os.remove(args.db)

    Session = init_db(args.db)
    with Session() as s:
        summary = Ingestor(s, args.case_id, args.dataset_dir).run()
    print_summary(summary)

    with Session() as s:
        analysis = run_full_analysis(s, args.case_id)
    print_analysis(analysis)

    with Session() as s:
        print("=" * 72)
        print("PATTERN DETECTION — investigative leads requiring human verification")
        print("=" * 72)

        chains = pdet.detect_pass_through_chains(s, args.case_id)
        print(f"\n[1] RAPID_PASS_THROUGH_CHAIN — {len(chains)} lead(s)")
        for a in chains:
            d = a["details"]
            print(f"  * {' -> '.join(d['transaction_ids'])}")
            print(f"    accounts {' -> '.join(d['account_path'])}")
            print(f"    holders  {' -> '.join(str(h) for h in d['account_holders'])}")
            print(f"    INR {d['amounts'][0]:,.0f} -> {d['amounts'][-1]:,.0f} over "
                  f"{d['elapsed_hours']}h, cuts {d['hop_cuts_pct']}%  [{a['severity']}]")
            if d["kyc_flags"]:
                print(f"    flags: {'; '.join(d['kyc_flags'])}")

        spikes = pdet.detect_communication_spikes(s, args.case_id)
        print(f"\n[2] COMMUNICATION_SPIKE — {len(spikes)} lead(s)")
        for a in spikes:
            d = a["details"]
            print(f"  * {d['person_a_name']} <-> {d['person_b_name']}")
            print(f"    {d['peak_contacts']} contacts on {d['peak_window'][0]}..{d['peak_window'][-1]}, "
                  f"baseline {d['baseline_contacts_per_day']:g}/day, ratio {d['ratio']}x, "
                  f"after {d['post_window_rate_per_day']}/day  [{a['severity']}]")

        geo = pdet.detect_geographic_cooccurrence(s, args.case_id)
        print(f"\n[3] GEOGRAPHIC_CO_OCCURRENCE — {len(geo)} lead(s)")
        for a in geo:
            d = a["details"]
            print(f"  * {d['site'].title()} {d['window_start'][:16]} - {d['window_end'][11:16]} "
                  f"via {'+'.join(d['source_kinds'])}  [{a['severity']}]")
            for p in d["participants"]:
                print(f"      - {p['name']:<22} placed by {'+'.join(p['placed_by'])}")
            if d["free_text_corroboration"]:
                print(f"      corroborated by "
                      f"{', '.join(c['document_id'] for c in d['free_text_corroboration'])}")

        print(f"\n[4] MULTI_HOP_CONNECTION — automatic cross-community discovery")
        paths = pdet.discover_bridging_paths(s, args.case_id)
        for p in paths[:6]:
            print(f"  * {p['endpoint_names'][0]} -> {p['endpoint_names'][1]}  "
                  f"({p['hop_count']} hops, {len(p['distinct_source_documents'])} source docs, "
                  f"min conf {p['path_confidence']})")
            for h in p["hops"]:
                print(f"      {h['from_name']} -[{h['relationship_type']}]-> {h['to_name']}")

        print("\n[5] CROSS-DETECTOR CORROBORATION — review priority")
        from backend.services.graph_builder import person_projection
        from backend.services.network_analysis import compute_metrics
        h = person_projection(s, args.case_id, min_edge_support=args.min_edge_support)
        btw = compute_metrics(h)["betweenness_centrality"]
        out = {"pass_through_chains": chains, "communication_spikes": spikes,
               "geographic_cooccurrence": geo, "bridging_paths": paths}
        for r in pdet.rank_by_corroboration(s, args.case_id, out, betweenness=btw)[:10]:
            print(f"  {r['family_count']} families | betweenness {r['betweenness']:.5f} | "
                  f"{r['name']:<22} {'+'.join(r['detector_families'])}")

        print("\n" + "=" * 72)
        print("All outputs above are potential connections and analytical indicators.")
        print("They require verification by an investigator and establish no culpability.")
        print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
