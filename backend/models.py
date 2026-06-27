# -*- coding: utf-8 -*-
"""
models.py — SQLAlchemy 資料模型（歷史記錄）
"""

import json
from datetime import datetime

from sqlalchemy import create_engine, Column, String, Float, Boolean, Text, DateTime, Integer
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class CheckRecord(Base):
    __tablename__ = "check_records"

    id            = Column(String, primary_key=True)
    timestamp     = Column(DateTime, default=datetime.utcnow)
    filename      = Column(String, nullable=False)
    member_name   = Column(String, default="")
    scene         = Column(String, default="")
    level         = Column(String, default="")
    score         = Column(Float,  default=0.0)
    passed        = Column(Boolean, default=False)
    stats_json    = Column(Text, default="{}")   # JSON string
    items_json    = Column(Text, default="[]")   # JSON string
    overall_comment = Column(Text, default="")

    def to_dict(self):
        return {
            "check_id":       self.id,
            "timestamp":      self.timestamp.isoformat() if self.timestamp else "",
            "filename":       self.filename,
            "member_name":    self.member_name,
            "scene":          self.scene,
            "level":          self.level,
            "score":          self.score,
            "passed":         self.passed,
            "stats":          json.loads(self.stats_json or "{}"),
            "items":          json.loads(self.items_json or "[]"),
            "overall_comment": self.overall_comment,
        }


# ── DB 初始化 ──────────────────────────────────────────────────────────

import os
from pathlib import Path

_DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{Path(__file__).parent}/data/history.db")
_engine = create_engine(_DB_URL, connect_args={"check_same_thread": False} if "sqlite" in _DB_URL else {})
Base.metadata.create_all(_engine)
SessionLocal = sessionmaker(bind=_engine)


def save_record(check_result: dict):
    """把一次檢核結果存入 DB。"""
    import json as _json
    session = SessionLocal()
    try:
        rec = CheckRecord(
            id              = check_result["check_id"],
            timestamp       = datetime.fromisoformat(check_result.get("timestamp", datetime.utcnow().isoformat())),
            filename        = check_result.get("filename", ""),
            member_name     = check_result.get("member_name", ""),
            scene           = check_result.get("scene", ""),
            level           = check_result.get("level", ""),
            score           = check_result.get("score", 0.0),
            passed          = check_result.get("passed", False),
            stats_json      = _json.dumps(check_result.get("stats", {}), ensure_ascii=False),
            items_json      = _json.dumps(check_result.get("items", []), ensure_ascii=False),
            overall_comment = check_result.get("overall_comment", ""),
        )
        session.add(rec)
        session.commit()
    finally:
        session.close()


def get_all_records(limit: int = 100) -> list[dict]:
    session = SessionLocal()
    try:
        recs = session.query(CheckRecord).order_by(CheckRecord.timestamp.desc()).limit(limit).all()
        return [r.to_dict() for r in recs]
    finally:
        session.close()


def get_record(check_id: str) -> dict | None:
    session = SessionLocal()
    try:
        rec = session.query(CheckRecord).filter(CheckRecord.id == check_id).first()
        return rec.to_dict() if rec else None
    finally:
        session.close()
