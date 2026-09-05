# SIH 26189 — backend data pipeline
=========================================================================================
Ingestion -> entity resolution -> graph -> network analysis -> rule-based pattern detection.
No ML and no LLM anywhere in this pipeline; every output is deterministic and traceable to
an Evidence row.

**Positioning:** nothing this pipeline emits asserts guilt. Alerts are investigative leads
with review priorities (INFORMATIONAL / REVIEW / PRIORITY_REVIEW), and every `reason`
string states the innocent reading alongside the indicator.

## Install & run

    pip install sqlalchemy networkx
    python -m backend.run_pipeline --case-id CASE-2026-DEMO --dataset-dir ./dataset --reset

Individual stages:

    python -m backend.services.ingest --case-id CASE-2026-DEMO --dataset-dir ./dataset
    python -m backend.services.network_analysis --case-id CASE-2026-DEMO
    python tests/test_entity_resolution.py
    python validate_against_ground_truth.py     # dev-only; reads the answer key

## Layout

    backend/models/db.py                  SQLAlchemy models (+2 additive columns, marked)
    backend/services/entity_resolution.py pure functions, unit-tested, no DB
    backend/services/ingest.py            idempotent structured loaders, fail-loud on bad FKs
    backend/services/graph_builder.py     MultiDiGraph + projections
    backend/services/network_analysis.py  degree / betweenness / pagerank / communities
    backend/services/pattern_detector.py  4 detectors + cross-detector corroboration
    backend/run_pipeline.py               end-to-end driver

## Not built yet (deliberately)

`TODO(nlp)` markers in ingest.py. FIR narratives and the surveillance/intelligence corpora
are stored verbatim on Document rows, split per report, and are NOT parsed. The geographic
detector's free-text corroboration is exact string matching against the alias index, not
entity extraction — it is a placeholder for the NER module.

## Dataset

`dataset/` holds the synthetic corpus this pipeline was developed against — 75 fictional
persons, 767 call records, 225 transactions, 11 FIRs and three embedded networks inside
background noise. All names, numbers, addresses, plates and case numbers are invented.

`dataset/ground_truth_answer_key.md` documents the patterns deliberately planted in that
corpus. It is a validation aid for the team, not demo material.

## Validation status

Run against the bundled dataset:

| Check | Result |
|---|---|
| Ingestion foreign keys | 0 unresolved |
| Idempotency | second run produces identical counts |
| Pass-through detector | both planted chains, 0 false positives at default thresholds |
| Communication spike | exactly 1 lead, the planted pair; 0 red herrings |
| Geographic co-occurrence | planted event recovered in full, corroborated by 2 field reports |
| Multi-hop path finder | works; does not reproduce the planted path (see below) |
| Betweenness centrality | does NOT isolate the bridge entity (see below) |
| Unit tests | 28 assertions passing |

Two known limitations, documented rather than tuned away:

1. **Betweenness alone is not a bridge detector.** A person with many repeated ordinary
   relationships outranks a genuine broker, because betweenness measures social centrality
   and cannot tell the two apart. `rank_by_corroboration()` — which ranks by how many
   independent detector families name a subject — separates them cleanly where centrality
   does not. Treat centrality as one input, not the headline.
2. **The path finder will not traverse shared-address edges by default.** Address equality
   is a weak signal (shared tenancies, multi-occupancy buildings), so those edges are
   confidence-0.2 and excluded from traversal and metrics. Pass `include_weak_edges=True`
   to override, knowing that unrelated co-tenants then appear one hop apart.
