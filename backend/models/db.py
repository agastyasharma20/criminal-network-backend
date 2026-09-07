"""
SQLAlchemy models for the SIH 26189 investigative analysis platform.

Reconstructed from the schema comment in the task brief. Two ADDITIVE columns were
required and are marked below; nothing from the original schema was removed or renamed.

IMPORTANT POSITIONING NOTE (applies to every string this module and its consumers emit):
nothing stored here asserts guilt. Entities are "subjects of interest", relationships are
"observed or inferred associations", and Alert rows are "investigative leads requiring
human verification". Downstream text must preserve that framing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class Case(Base):
    __tablename__ = "cases"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    case_type = Column(String)
    status = Column(String, default="OPEN")
    created_at = Column(DateTime, default=utcnow)


class Document(Base):
    """A source of evidence. For structured ingestion one Document is created per source
    FILE (id == the filename), so every derived row can point back at its origin."""
    __tablename__ = "documents"
    id = Column(String, primary_key=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    document_type = Column(String)          # STRUCTURED_CSV | STRUCTURED_JSON | FIR | SURVEILLANCE | INTEL
    text = Column(Text)                     # free-text body, when the source has one (e.g. FIR narrative)
    uploaded_at = Column(DateTime, default=utcnow)


class Entity(Base):
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    type = Column(String, nullable=False)   # PERSON | PHONE | VEHICLE | BANK_ACCOUNT | LOCATION | ...
    name = Column(String, nullable=False)
    normalized_name = Column(String, index=True)
    entity_metadata = Column(Text)          # JSON blob

    # ADDITIVE: stable natural key from the source data ("PERSON:P0101", "PHONE:+919000011012").
    # Required to make ingestion idempotent — without it a second run cannot tell an existing
    # row from a new one, since names are not unique.
    natural_id = Column(String, index=True)

    __table_args__ = (UniqueConstraint("case_id", "natural_id", name="uq_entity_natural"),)

    @property
    def meta(self) -> dict:
        return json.loads(self.entity_metadata) if self.entity_metadata else {}


class RelationshipRow(Base):
    __tablename__ = "relationships"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    source_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False, index=True)
    target_entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False, index=True)
    relationship_type = Column(String, nullable=False)
    confidence = Column(Float, default=1.0)
    weight = Column(Float, default=1.0)
    occurred_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)

    # ADDITIVE: same rationale as Entity.natural_id (idempotent upsert), plus a small JSON
    # payload so structural detectors can read facts like transaction amount or call duration
    # without a second round-trip to the raw CSV.
    natural_id = Column(String, index=True)
    rel_metadata = Column(Text)

    __table_args__ = (UniqueConstraint("case_id", "natural_id", name="uq_rel_natural"),)

    @property
    def meta(self) -> dict:
        return json.loads(self.rel_metadata) if self.rel_metadata else {}


class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(Integer, primary_key=True, autoincrement=True)
    relationship_id = Column(Integer, ForeignKey("relationships.id"), nullable=False, index=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    page = Column(String)                   # for CSV sources: the source row identifier
    text_snippet = Column(Text)
    confidence = Column(Float, default=1.0)


class Analysis(Base):
    __tablename__ = "analysis"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False, index=True)
    metric = Column(String, nullable=False)
    value = Column(Float)
    computed_at = Column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_analysis_case_entity", "case_id", "entity_id"),)


class Alert(Base):
    """An investigative LEAD. Never a finding of wrongdoing."""
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False, index=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), index=True)
    type = Column(String, nullable=False)
    severity = Column(String)               # INFORMATIONAL | REVIEW | PRIORITY_REVIEW
    reason = Column(Text)
    evidence_ids = Column(Text)             # JSON list

    __table_args__ = (Index("ix_alert_case_entity", "case_id", "entity_id"),)


# --------------------------------------------------------------------------- session
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str = "sih26189.db"):
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(db_path: str = "sih26189.db"):
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)

