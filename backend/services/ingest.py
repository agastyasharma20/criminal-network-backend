"""
Structured ingestion for SIH 26189.

Loads the CSV/JSON sources straight into Entity / RelationshipRow / Evidence. No NLP here:
the free-text bodies (FIR narratives, surveillance reports, intelligence briefs) are stored
verbatim on Document rows for the NER module to consume later. See TODO(nlp) markers.

Idempotency
-----------
Every Entity and RelationshipRow carries a `natural_id` derived from the source data
(e.g. "PERSON:P0101", "TXN:TXN-0133"). Loading is upsert-by-natural_id, so running the
ingest twice produces the same row count. Evidence is keyed off the relationship, so it is
replaced rather than appended on re-ingest.

CDR granularity — decision
--------------------------
Both, at different levels, because the two consumers want different things:

  * PHONE --CALLS--> PHONE, one row per call, occurred_at = timestamp, weight = duration.
    The communication-spike detector needs per-day counts per ordered pair; aggregating at
    ingest would destroy exactly the signal it looks for.
  * PERSON --CALLS--> PERSON, one aggregated row per pair, weight = number of calls.
    Centrality and community detection want one weighted edge per pair of people, not 767
    parallel edges that would distort degree and betweenness.

Additionally each call emits PHONE --PRESENT_AT--> LOCATION(cell tower), which is what lets
the geographic co-occurrence detector treat a tower ping and an ANPR hit as the same kind
of fact from two independent sources.

Run standalone:
    python -m backend.services.ingest --case-id CASE-2026-DEMO --dataset-dir ./dataset
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import delete, select

from backend.models.db import (
    Alert, Analysis, Case, Document, Entity, Evidence, RelationshipRow, init_db,
)
from backend.services import entity_resolution as er

EXTERNAL_CASH = "EXTERNAL-CASH"


class IngestError(Exception):
    pass


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class Ingestor:
    def __init__(self, session, case_id: str, dataset_dir: str, *, strict: bool = False):
        self.s = session
        self.case_id = case_id
        self.dir = dataset_dir
        self.strict = strict
        self._entity_cache: dict[str, Entity] = {}
        self._rel_cache: dict[str, RelationshipRow] = {}
        self.unresolved: list[dict] = []
        self.stats_entities = Counter()
        self.stats_rels = Counter()
        self.soft_flags: list[dict] = []

    # ------------------------------------------------------------------ io helpers
    def path(self, name: str) -> str:
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            raise IngestError(f"required source file missing: {p}")
        return p

    def read_csv(self, name: str) -> list[dict]:
        with open(self.path(name), newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def fail(self, source_file: str, row_id: str, reason: str):
        """Record an unresolvable reference. Fail loud: never silently drop a row."""
        rec = {"source_file": source_file, "row_id": row_id, "reason": reason}
        self.unresolved.append(rec)
        if self.strict:
            raise IngestError(f"{source_file}[{row_id}]: {reason}")

    # ------------------------------------------------------------------ upserts
    def document(self, doc_id: str, filename: str, doc_type: str, text: str | None = None) -> Document:
        doc = self.s.get(Document, doc_id)
        if doc is None:
            doc = Document(id=doc_id, case_id=self.case_id, filename=filename,
                           document_type=doc_type, text=text)
            self.s.add(doc)
            self.s.flush()
        elif text is not None and doc.text != text:
            doc.text = text
        return doc

    def entity(self, etype: str, natural_key: str, name: str, meta: dict | None = None) -> Entity:
        nid = f"{etype}:{natural_key}"
        if nid in self._entity_cache:
            ent = self._entity_cache[nid]
            if meta:
                merged = ent.meta | meta
                ent.entity_metadata = json.dumps(merged, default=str)
            return ent
        ent = self.s.scalar(
            select(Entity).where(Entity.case_id == self.case_id, Entity.natural_id == nid)
        )
        if ent is None:
            ent = Entity(case_id=self.case_id, type=etype, name=name, natural_id=nid,
                         normalized_name=er.normalize_name(name),
                         entity_metadata=json.dumps(meta or {}, default=str))
            self.s.add(ent)
            self.s.flush()
            self.stats_entities[etype] += 1
        else:
            ent.name = name
            ent.normalized_name = er.normalize_name(name)
            if meta:
                ent.entity_metadata = json.dumps(ent.meta | meta, default=str)
        self._entity_cache[nid] = ent
        return ent

    def rel(self, src: Entity, dst: Entity, rtype: str, natural_key: str, *,
            confidence: float = 1.0, weight: float = 1.0, occurred_at: datetime | None = None,
            meta: dict | None = None,
            evidence: tuple[str, str, str] | None = None) -> RelationshipRow:
        """evidence = (document_id, page/row-identifier, snippet)"""
        nid = f"{rtype}:{natural_key}"
        row = self._rel_cache.get(nid) or self.s.scalar(
            select(RelationshipRow).where(
                RelationshipRow.case_id == self.case_id, RelationshipRow.natural_id == nid)
        )
        if row is None:
            row = RelationshipRow(
                case_id=self.case_id, source_entity_id=src.id, target_entity_id=dst.id,
                relationship_type=rtype, confidence=confidence, weight=weight,
                occurred_at=occurred_at, natural_id=nid,
                rel_metadata=json.dumps(meta or {}, default=str))
            self.s.add(row)
            self.s.flush()
            self.stats_rels[rtype] += 1
        else:
            row.source_entity_id, row.target_entity_id = src.id, dst.id
            row.confidence, row.weight, row.occurred_at = confidence, weight, occurred_at
            row.rel_metadata = json.dumps(meta or {}, default=str)
        self._rel_cache[nid] = row

        if evidence:
            doc_id, page, snippet = evidence
            self.s.execute(delete(Evidence).where(Evidence.relationship_id == row.id))
            self.s.add(Evidence(relationship_id=row.id, document_id=doc_id, page=page,
                                text_snippet=snippet, confidence=confidence))
        return row

    # ------------------------------------------------------------------ loaders
    def load_persons(self):
        f = "persons.csv"
        doc = self.document(f, f, "STRUCTURED_CSV")
        rows = self.read_csv(f)
        for r in rows:
            aliases = [a for a in (r.get("aliases") or "").split("|") if a]
            self.entity("PERSON", r["person_id"], r["full_name"], {
                "aliases": aliases,
                "dob": r.get("dob"), "gender": r.get("gender"), "address": r.get("address"),
                "occupation": r.get("occupation"),
                "id_proof_type": r.get("id_proof_type"),
                "id_proof_number": r.get("id_proof_number"),
                "first_seen_case": r.get("first_seen_case"),
                "source": f,
            })
        self._persons = rows
        self._person_name = {r["person_id"]: r["full_name"] for r in rows}
        self.alias_index = er.build_alias_index(rows)
        return doc

    def load_phones(self):
        f = "phone_numbers.csv"
        self.document(f, f, "STRUCTURED_CSV")
        rows = self.read_csv(f)
        self.phone_owner: dict[str, str] = {}
        for r in rows:
            canonical = er.normalize_phone(r["number"])
            if not er.is_plausible_phone(canonical):
                self.fail(f, r.get("phone_id", "?"), f"unparseable phone number {r['number']!r}")
                continue
            ph = self.entity("PHONE", canonical, canonical, {
                "phone_id": r["phone_id"], "raw_number": r["number"],
                "sim_registered_name": r.get("sim_registered_name"),
                "activation_date": r.get("activation_date"), "status": r.get("status"),
                "source": f,
            })
            owner_nid = f"PERSON:{r['owner_person_id']}"
            owner = self._entity_cache.get(owner_nid)
            if owner is None:
                self.fail(f, r["phone_id"], f"owner_person_id {r['owner_person_id']} not in persons.csv")
                continue
            self.phone_owner[canonical] = r["owner_person_id"]
            self.rel(owner, ph, "OWNS", f"{r['owner_person_id']}:{canonical}",
                     evidence=(f, r["phone_id"],
                               f"{r['owner_person_id']} recorded as subscriber of {canonical}"))

            # Challenge 3: registered name != actual user. Association, never a merge.
            mm = er.detect_sim_name_mismatch(r, self._person_name, self.alias_index)
            if mm:
                self.soft_flags.append(mm | {"flag": "SIM_NAME_MISMATCH"})
                if mm["registered_person_id"]:
                    other = self._entity_cache.get(f"PERSON:{mm['registered_person_id']}")
                    if other is not None and mm["registered_person_id"] != r["owner_person_id"]:
                        self.rel(other, owner, "ASSOCIATED_WITH",
                                 f"simreg:{r['phone_id']}", confidence=0.6,
                                 meta={"basis": "SIM_REGISTERED_TO_THIRD_PARTY",
                                       "phone": canonical,
                                       "requires_human_review": True},
                                 evidence=(f, r["phone_id"],
                                           f"SIM {canonical} used by {r['owner_person_id']} is "
                                           f"registered in the name of {mm['registered_name']}. "
                                           f"Recorded as an association for review; no conclusion drawn."))

    def load_cell_towers(self):
        f = "cell_towers.csv"
        self.document(f, f, "STRUCTURED_CSV")
        for r in self.read_csv(f):
            self.entity("LOCATION", r["cell_tower_id"], r["location_name"], {
                "kind": "CELL_TOWER", "area": r.get("area"),
                "latitude": r.get("latitude"), "longitude": r.get("longitude"),
                "source": f,
            })

    def load_cdr(self):
        f = "cdr.csv"
        self.document(f, f, "STRUCTURED_CSV")
        rows = self.read_csv(f)
        pair_counter: Counter = Counter()
        pair_first: dict[tuple[str, str], datetime] = {}

        for r in rows:
            a = er.normalize_phone(r["caller_number"])
            b = er.normalize_phone(r["receiver_number"])
            ts = parse_ts(r["timestamp"])
            if not a or not b:
                self.fail(f, r["call_id"], "unparseable caller/receiver number")
                continue
            ea = self._entity_cache.get(f"PHONE:{a}")
            eb = self._entity_cache.get(f"PHONE:{b}")
            if ea is None or eb is None:
                missing = a if ea is None else b
                self.fail(f, r["call_id"], f"number {missing} not present in phone_numbers.csv")
                continue

            self.rel(ea, eb, "CALLS", r["call_id"], weight=float(r.get("duration_seconds") or 0),
                     occurred_at=ts,
                     meta={"call_type": r.get("call_type"),
                           "duration_seconds": r.get("duration_seconds"),
                           "cell_tower_id": r.get("cell_tower_id")},
                     evidence=(f, r["call_id"],
                               f"{a} -> {b} at {r['timestamp']} "
                               f"({r.get('duration_seconds')}s, {r.get('call_type')})"))

            tower = self._entity_cache.get(f"LOCATION:{r.get('cell_tower_id')}")
            if tower is None:
                self.fail(f, r["call_id"], f"cell_tower_id {r.get('cell_tower_id')} not in cell_towers.csv")
            else:
                self.rel(ea, tower, "PRESENT_AT", f"cdr:{r['call_id']}", confidence=0.8,
                         occurred_at=ts,
                         meta={"source_kind": "CDR_CELL_SITE", "phone": a},
                         evidence=(f, r["call_id"],
                                   f"Handset {a} served by {r['cell_tower_id']} at {r['timestamp']}"))

            pa, pb = self.phone_owner.get(a), self.phone_owner.get(b)
            if pa and pb and pa != pb:
                key = (pa, pb)
                pair_counter[key] += 1
                if ts and (key not in pair_first or ts < pair_first[key]):
                    pair_first[key] = ts

        for (pa, pb), n in pair_counter.items():
            sa, sb = self._entity_cache[f"PERSON:{pa}"], self._entity_cache[f"PERSON:{pb}"]
            self.rel(sa, sb, "CALLS", f"agg:{pa}:{pb}", weight=float(n),
                     occurred_at=pair_first.get((pa, pb)),
                     meta={"aggregate": True, "call_count": n},
                     evidence=(f, f"aggregate:{pa}->{pb}",
                               f"{n} call detail records associate {pa} with {pb}"))

    def load_bank_accounts(self):
        f = "bank_accounts.csv"
        self.document(f, f, "STRUCTURED_CSV")
        self.account_holder: dict[str, str] = {}
        for r in self.read_csv(f):
            acc = self.entity("BANK_ACCOUNT", r["account_id"],
                              f"{r['account_id']} ({r.get('bank_name')})", {
                "bank_name": r.get("bank_name"), "account_number": r.get("account_number"),
                "account_type": r.get("account_type"), "kyc_status": r.get("kyc_status"),
                "opening_date": r.get("opening_date"),
                "account_holder_person_id": r.get("account_holder_person_id"),
                "source": f,
            })
            holder = self._entity_cache.get(f"PERSON:{r['account_holder_person_id']}")
            if holder is None:
                self.fail(f, r["account_id"],
                          f"account_holder_person_id {r['account_holder_person_id']} not in persons.csv")
                continue
            self.account_holder[r["account_id"]] = r["account_holder_person_id"]
            self.rel(holder, acc, "OWNS", f"{r['account_holder_person_id']}:{r['account_id']}",
                     evidence=(f, r["account_id"],
                               f"{r['account_holder_person_id']} recorded as holder of {r['account_id']}"))

    def load_transactions(self):
        f = "transactions.csv"
        self.document(f, f, "STRUCTURED_CSV")
        for r in self.read_csv(f):
            src_id, dst_id = r["sender_account_id"], r["receiver_account_id"]
            ends = {}
            for role, aid in (("src", src_id), ("dst", dst_id)):
                if aid == EXTERNAL_CASH:
                    ends[role] = self.entity("BANK_ACCOUNT", EXTERNAL_CASH,
                                             "External cash (outside the banking channel)",
                                             {"kind": "EXTERNAL_CASH", "source": f})
                else:
                    e = self._entity_cache.get(f"BANK_ACCOUNT:{aid}")
                    if e is None:
                        self.fail(f, r["transaction_id"], f"account {aid} not in bank_accounts.csv")
                    ends[role] = e
            if ends["src"] is None or ends["dst"] is None:
                continue
            try:
                amount = float(r["amount_inr"])
            except (TypeError, ValueError):
                self.fail(f, r["transaction_id"], f"unparseable amount {r.get('amount_inr')!r}")
                continue
            self.rel(ends["src"], ends["dst"], "TRANSFERRED_TO", r["transaction_id"],
                     weight=amount, occurred_at=parse_ts(r["timestamp"]),
                     meta={"amount_inr": amount, "transaction_type": r.get("transaction_type"),
                           "bank_name": r.get("bank_name"), "remarks": r.get("remarks"),
                           "sender_account_id": src_id, "receiver_account_id": dst_id},
                     evidence=(f, r["transaction_id"],
                               f"{src_id} -> {dst_id} INR {amount:,.0f} on {r['timestamp']} "
                               f"({r.get('transaction_type')}) - {r.get('remarks')}"))

    def load_vehicles(self):
        f = "vehicles.csv"
        self.document(f, f, "STRUCTURED_CSV")
        for r in self.read_csv(f):
            veh = self.entity("VEHICLE", r["vehicle_id"], r["registration_number"], {
                "registration_number": r["registration_number"], "make_model": r.get("make_model"),
                "color": r.get("color"), "registration_date": r.get("registration_date"),
                "registered_address": r.get("registered_address"),
                "owner_person_id": r.get("owner_person_id"), "source": f,
            })
            owner = self._entity_cache.get(f"PERSON:{r['owner_person_id']}")
            if owner is None:
                self.fail(f, r["vehicle_id"],
                          f"owner_person_id {r['owner_person_id']} not in persons.csv")
                continue
            self.rel(owner, veh, "OWNS", f"{r['owner_person_id']}:{r['vehicle_id']}",
                     evidence=(f, r["vehicle_id"],
                               f"{r['registration_number']} registered to {r['owner_person_id']} "
                               f"at {r.get('registered_address')}"))

    def load_vehicle_movements(self):
        f = "vehicle_movement_logs.csv"
        self.document(f, f, "STRUCTURED_CSV")
        for r in self.read_csv(f):
            veh = self._entity_cache.get(f"VEHICLE:{r['vehicle_id']}")
            if veh is None:
                self.fail(f, r["log_id"], f"vehicle_id {r['vehicle_id']} not in vehicles.csv")
                continue
            loc = self.entity("LOCATION", r["camera_id"], r["location_name"],
                              {"kind": "ANPR_CAMERA", "camera_id": r["camera_id"], "source": f})
            self.rel(veh, loc, "PRESENT_AT", f"anpr:{r['log_id']}", confidence=0.95,
                     occurred_at=parse_ts(r["timestamp"]),
                     meta={"source_kind": "ANPR", "camera_id": r["camera_id"]},
                     evidence=(f, r["log_id"],
                               f"{r['vehicle_id']} captured by {r['camera_id']} "
                               f"({r['location_name']}) at {r['timestamp']}"))

    def load_organizations(self):
        f = "organizations.csv"
        self.document(f, f, "STRUCTURED_CSV")
        for r in self.read_csv(f):
            org = self.entity("ORGANIZATION", r["org_id"], r["org_name"], {
                "registration_number": r.get("registration_number"),
                "business_type": r.get("business_type"),
                "registered_address": r.get("registered_address"), "source": f,
            })
            for pid in [p for p in (r.get("linked_person_ids") or "").split("|") if p]:
                person = self._entity_cache.get(f"PERSON:{pid}")
                if person is None:
                    self.fail(f, r["org_id"], f"linked person {pid} not in persons.csv")
                    continue
                self.rel(person, org, "ASSOCIATED_WITH", f"{pid}:{r['org_id']}", confidence=0.9,
                         meta={"basis": "LISTED_IN_ORGANIZATION_RECORD"},
                         evidence=(f, r["org_id"],
                                   f"{pid} listed against {r['org_name']} "
                                   f"({r.get('registration_number')})"))

    def load_firs(self):
        f = "firs.json"
        with open(self.path(f), encoding="utf-8") as fh:
            firs = json.load(fh)
        self.document(f, f, "STRUCTURED_JSON")
        self.fir_ids = set()
        for fir in firs:
            fid = fir["fir_id"]
            self.fir_ids.add(fid)
            # TODO(nlp): narrative_text is stored verbatim and NOT parsed here. The NER /
            # relation-extraction module will read Document.text and emit additional
            # MENTIONED_IN edges with confidence < 1.0 for entities found only in prose.
            self.document(fid, f, "FIR", text=fir.get("narrative_text"))
            doc_ent = self.entity("DOCUMENT", fid, fid, {
                "police_station": fir.get("police_station"), "date_filed": fir.get("date_filed"),
                "sections_applied": fir.get("sections_applied"), "status": fir.get("status"),
                "narrative_length": len(fir.get("narrative_text") or ""),
                "narrative_parsed": False, "source": f,
            })
            named = list(dict.fromkeys(
                ([fir["complainant_person_id"]] if fir.get("complainant_person_id") else [])
                + list(fir.get("named_persons") or [])))
            for pid in named:
                person = self._entity_cache.get(f"PERSON:{pid}")
                if person is None:
                    self.fail(f, fid, f"named/complainant person {pid} not in persons.csv")
                    continue
                role = "complainant" if pid == fir.get("complainant_person_id") else "named party"
                self.rel(person, doc_ent, "MENTIONED_IN", f"{pid}:{fid}", confidence=1.0,
                         occurred_at=parse_ts(fir.get("date_filed")),
                         meta={"role": role,
                               "note": "Named in the report. Carries no implication of culpability."},
                         evidence=(fid, fid,
                                   f"{pid} appears in {fid} ({fir.get('police_station')}) as {role}."))

    def load_case_files(self):
        f = "case_files.csv"
        self.document(f, f, "STRUCTURED_CSV")
        for r in self.read_csv(f):
            case_ent = self.entity("CASE", r["case_id"], r["case_name"], {
                "opened_date": r.get("opened_date"), "status": r.get("status"),
                "lead_investigator": r.get("lead_investigator"), "source": f,
            })
            for fid in [x for x in (r.get("linked_fir_ids") or "").split("|") if x]:
                doc_ent = self._entity_cache.get(f"DOCUMENT:{fid}")
                if doc_ent is None:
                    self.fail(f, r["case_id"], f"linked FIR {fid} not present in firs.json")
                    continue
                self.rel(doc_ent, case_ent, "INVOLVED_IN", f"{fid}:{r['case_id']}",
                         evidence=(f, r["case_id"], f"{fid} filed under {r['case_id']} ({r['case_name']})"))

    def load_free_text_stubs(self):
        """Store the narrative corpora as Documents. NOT parsed here.

        TODO(nlp): surveillance_reports.txt and intelligence_reports.txt are split into
        per-report Documents so the NER module can process one report at a time and attach
        MENTIONED_IN / PRESENT_AT edges with source-appropriate confidence. Intelligence
        briefs use hedged language ("suspected", "unconfirmed") and must be ingested at
        reduced confidence rather than as assertions of fact.
        """
        import re
        for fname, doctype, pattern in [
            ("surveillance_reports.txt", "SURVEILLANCE", r"Report No:\s*(SR-\d+)"),
            ("intelligence_reports.txt", "INTEL", r"Ref:\s*(INT-[\w-]+)"),
        ]:
            p = os.path.join(self.dir, fname)
            if not os.path.exists(p):
                continue
            body = open(p, encoding="utf-8").read()
            self.document(fname, fname, doctype, text=body)
            ids = re.findall(pattern, body)
            marks = [body.index(f"{i}") for i in ids]
            for n, (rid, start) in enumerate(zip(ids, marks)):
                end = marks[n + 1] if n + 1 < len(marks) else len(body)
                self.document(rid, fname, doctype, text=body[start:end].strip())
                self.entity("DOCUMENT", rid, rid,
                            {"source": fname, "kind": doctype, "narrative_parsed": False})

    def load_shared_address_flags(self):
        """Challenge 4: soft candidates only. Confidence 0.2, review-required, never a merge."""
        for cand in er.shared_address_candidates(self._persons):
            a = self._entity_cache.get(f"PERSON:{cand['person_a']}")
            b = self._entity_cache.get(f"PERSON:{cand['person_b']}")
            if a is None or b is None:
                continue
            self.soft_flags.append(cand | {"flag": "SHARED_ADDRESS"})
            self.rel(a, b, "ASSOCIATED_WITH",
                     f"addr:{cand['person_a']}:{cand['person_b']}",
                     confidence=cand["confidence"],
                     meta={"basis": "SHARED_REGISTERED_ADDRESS",
                           "requires_human_review": True,
                           "excluded_from_metrics": True,
                           "note": cand["note"]},
                     evidence=("persons.csv", f"{cand['person_a']}|{cand['person_b']}",
                               f"{cand['person_a']} and {cand['person_b']} share a registered "
                               f"address. Weak indicator only — shared tenancy is common."))

    # ------------------------------------------------------------------ driver
    def run(self) -> dict:
        if self.s.get(Case, self.case_id) is None:
            self.s.add(Case(id=self.case_id, name=f"Ingested dataset {self.case_id}",
                            case_type="NETWORK_ANALYSIS", status="OPEN"))
            self.s.flush()

        self.load_persons()
        self.load_phones()
        self.load_cell_towers()
        self.load_cdr()
        self.load_bank_accounts()
        self.load_transactions()
        self.load_vehicles()
        self.load_vehicle_movements()
        self.load_organizations()
        self.load_firs()
        self.load_case_files()
        self.load_free_text_stubs()
        self.load_shared_address_flags()
        self.s.commit()

        ent_counts = Counter(
            t for (t,) in self.s.execute(select(Entity.type).where(Entity.case_id == self.case_id))
        )
        rel_counts = Counter(
            t for (t,) in self.s.execute(
                select(RelationshipRow.relationship_type).where(RelationshipRow.case_id == self.case_id))
        )
        ev_count = self.s.scalar(select(Evidence.id).order_by(Evidence.id.desc()).limit(1)) or 0
        ev_total = len(list(self.s.execute(select(Evidence.id))))
        return {
            "case_id": self.case_id,
            "entities_by_type": dict(sorted(ent_counts.items())),
            "entities_total": sum(ent_counts.values()),
            "relationships_by_type": dict(sorted(rel_counts.items())),
            "relationships_total": sum(rel_counts.values()),
            "evidence_total": ev_total,
            "unresolved": self.unresolved,
            "soft_flags": self.soft_flags,
        }


def print_summary(summary: dict):
    print("=" * 72)
    print(f"INGESTION SUMMARY — case {summary['case_id']}")
    print("=" * 72)
    print(f"\nEntities ({summary['entities_total']}):")
    for t, n in summary["entities_by_type"].items():
        print(f"   {t:<14} {n:>6}")
    print(f"\nRelationships ({summary['relationships_total']}):")
    for t, n in summary["relationships_by_type"].items():
        print(f"   {t:<18} {n:>6}")
    print(f"\nEvidence rows: {summary['evidence_total']}")

    flags = Counter(f["flag"] for f in summary["soft_flags"])
    print(f"\nResolution flags raised (for analyst review, no conclusions drawn):")
    for k, n in sorted(flags.items()):
        print(f"   {k:<22} {n:>4}")
    for f in summary["soft_flags"]:
        if f["flag"] == "SIM_NAME_MISMATCH":
            print(f"      SIM {f['number']} used by {f['actual_owner_person_id']} "
                  f"but registered as {f['registered_name']!r} "
                  f"-> {f['registered_person_id'] or 'name not on record'}")

    unres = summary["unresolved"]
    print(f"\nUnresolved references: {len(unres)}")
    for u in unres[:40]:
        print(f"   ! {u['source_file']}[{u['row_id']}] {u['reason']}")
    if len(unres) > 40:
        print(f"   ... and {len(unres) - 40} more")
    print("=" * 72)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingest structured investigation sources.")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--db", default="sih26189.db")
    ap.add_argument("--strict", action="store_true",
                    help="abort on the first unresolvable foreign key instead of collecting them")
    args = ap.parse_args(argv)

    Session = init_db(args.db)
    with Session() as s:
        ing = Ingestor(s, args.case_id, args.dataset_dir, strict=args.strict)
        summary = ing.run()
    print_summary(summary)
    return 0 if not summary["unresolved"] else 0   # unresolved rows are reported, not fatal


if __name__ == "__main__":
    sys.exit(main())
