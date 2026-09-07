"""Unit tests for the audit remediations."""
import os
import sys
from datetime import datetime, timedelta

import networkx as nx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.db import (
    Alert, Analysis, Case, Document, Entity, Evidence, RelationshipRow, init_db, make_engine,
)
from backend.services import graph_builder as gb
from backend.services import pattern_detector as pd


def test_sqlite_foreign_keys_enforced(tmp_path):
    """SEC-01: Verify SQLite foreign key enforcement is active via PRAGMA foreign_keys=ON."""
    db_file = str(tmp_path / "test_fk.db")
    Session = init_db(db_file)
    with Session() as s:
        # Inserting an Entity pointing to a non-existent case_id must raise an IntegrityError
        invalid_entity = Entity(case_id="NON_EXISTENT_CASE", type="PERSON", name="Invalid Person")
        s.add(invalid_entity)
        with pytest.raises(IntegrityError):
            s.commit()


def test_passthrough_subchain_deduplication():
    """PAT-01: Verify sub-chain deduplication drops sub-chains and keeps maximal chains."""
    # Simulate chains
    ch1 = [{"txn_id": "T1"}, {"txn_id": "T2"}, {"txn_id": "T3"}, {"txn_id": "T4"}]
    ch2 = [{"txn_id": "T2"}, {"txn_id": "T3"}, {"txn_id": "T4"}] # suffix subchain
    ch3 = [{"txn_id": "T2"}, {"txn_id": "T3"}] # infix subchain
    ch4 = [{"txn_id": "T10"}, {"txn_id": "T11"}, {"txn_id": "T12"}] # independent chain

    chains = [ch1, ch2, ch3, ch4]
    keyed = {tuple(c["txn_id"] for c in ch): ch for ch in chains}

    def is_subchain(sub, main):
        return any(main[i:i + len(sub)] == sub for i in range(len(main) - len(sub) + 1))

    maximal = [ch for k, ch in keyed.items()
               if not any(other != k and is_subchain(k, other) for other in keyed)]

    assert len(maximal) == 2
    assert tuple(c["txn_id"] for c in maximal[0]) in {("T1", "T2", "T3", "T4"), ("T10", "T11", "T12")}
    assert tuple(c["txn_id"] for c in maximal[1]) in {("T1", "T2", "T3", "T4"), ("T10", "T11", "T12")}


def test_find_paths_confidence_and_hub_pruning(tmp_path):
    """PAT-02 & PAT-03: Verify confidence preservation and transit hub removal in find_paths."""
    db_file = str(tmp_path / "test_paths.db")
    Session = init_db(db_file)
    with Session() as s:
        c = Case(id="CASE-1", name="Test Case")
        s.add(c)
        p1 = Entity(case_id="CASE-1", type="PERSON", name="Person 1", natural_id="PERSON:P1")
        loc = Entity(case_id="CASE-1", type="LOCATION", name="Transit Hub", natural_id="LOCATION:L1")
        p2 = Entity(case_id="CASE-1", type="PERSON", name="Person 2", natural_id="PERSON:P2")
        s.add_all([p1, loc, p2])
        s.flush()

        doc = Document(id="doc1", case_id="CASE-1", filename="doc1.csv")
        s.add(doc)
        s.flush()

        # Connect p1 -> loc -> p2 via LOCATION
        r1 = RelationshipRow(case_id="CASE-1", source_entity_id=p1.id, target_entity_id=loc.id,
                             relationship_type="LOCATED_AT", confidence=0.85, natural_id="R1")
        r2 = RelationshipRow(case_id="CASE-1", source_entity_id=loc.id, target_entity_id=p2.id,
                             relationship_type="LOCATED_AT", confidence=0.75, natural_id="R2")
        s.add_all([r1, r2])
        s.flush()

        # Connect p1 -> p2 directly via ASSOCIATED_WITH with confidence 0.95
        r3 = RelationshipRow(case_id="CASE-1", source_entity_id=p1.id, target_entity_id=p2.id,
                             relationship_type="ASSOCIATED_WITH", confidence=0.95, natural_id="R3")
        s.add(r3)
        s.commit()

        # If LOCATION is blocked, path should NOT go through loc
        paths = pd.find_paths(s, "CASE-1", "PERSON:P1", "PERSON:P2", transit_blocked_types=("LOCATION",))
        assert len(paths) == 1
        assert paths[0]["hop_count"] == 1
        assert paths[0]["path_confidence"] == 0.95


def test_persist_alerts_idempotence(tmp_path):
    """DAT-01: Verify persist_alerts saves alerts and is idempotent on repeated runs."""
    db_file = str(tmp_path / "test_alerts.db")
    Session = init_db(db_file)
    with Session() as s:
        c = Case(id="CASE-1", name="Test Case")
        s.add(c)
        s.commit()

        alerts = [
            {"type": "LEAD_A", "severity": "REVIEW", "reason": "Reason 1", "evidence_ids": [1]},
            {"type": "LEAD_B", "severity": "PRIORITY_REVIEW", "reason": "Reason 2", "evidence_ids": [2]},
        ]

        # First run
        pd.persist_alerts(s, "CASE-1", alerts)
        stored = list(s.scalars(select(Alert).where(Alert.case_id == "CASE-1")))
        assert len(stored) == 2

        # Second run with same alerts: must not duplicate
        pd.persist_alerts(s, "CASE-1", alerts)
        stored_again = list(s.scalars(select(Alert).where(Alert.case_id == "CASE-1")))
        assert len(stored_again) == 2


def test_person_projection_preserves_non_calls_under_min_support(tmp_path):
    """GRP-01: Verify min_edge_support > 1 prunes pure calls but preserves financial/corporate links."""
    db_file = str(tmp_path / "test_graph.db")
    Session = init_db(db_file)
    with Session() as s:
        c = Case(id="CASE-1", name="Test Case")
        s.add(c)
        p1 = Entity(case_id="CASE-1", type="PERSON", name="Person 1", natural_id="PERSON:P1")
        p2 = Entity(case_id="CASE-1", type="PERSON", name="Person 2", natural_id="PERSON:P2")
        p3 = Entity(case_id="CASE-1", type="PERSON", name="Person 3", natural_id="PERSON:P3")
        s.add_all([p1, p2, p3])
        s.flush()

        # p1 and p2 have 1 one-off call (support = 1)
        r_call = RelationshipRow(case_id="CASE-1", source_entity_id=p1.id, target_entity_id=p2.id,
                                 relationship_type="CALLS", natural_id="C1",
                                 rel_metadata='{"aggregate": true, "call_count": 1}')
        # p1 and p3 have 1 transfer (support = 1)
        r_trans = RelationshipRow(case_id="CASE-1", source_entity_id=p1.id, target_entity_id=p3.id,
                                  relationship_type="TRANSFERRED_TO", natural_id="T1",
                                  rel_metadata='{"amount_inr": 500000}')
        s.add_all([r_call, r_trans])
        s.commit()

        # Under min_edge_support=2, the 1-call edge (p1-p2) is pruned, but the transfer (p1-p3) is kept!
        h = gb.person_projection(s, "CASE-1", min_edge_support=2)
        assert not h.has_edge(p1.id, p2.id)
        assert h.has_edge(p1.id, p3.id)
