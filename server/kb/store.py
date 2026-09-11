"""知识库存取:SQLite + 按 source 幂等 upsert + 过滤检索。"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from .models import Base, KnowledgeEntry

DEFAULT_DB = Path.home() / ".opennano" / "opennano.db"


class KBStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    # ---- 写 ----
    def upsert(self, entry: dict) -> tuple[KnowledgeEntry, bool]:
        """按 source 幂等写入。返回 (entry, created)。"""
        with self.Session() as s:
            src = entry.get("source", "")
            obj = s.scalar(select(KnowledgeEntry).where(KnowledgeEntry.source == src))
            created = obj is None
            if obj is None:
                obj = KnowledgeEntry(
                    id=entry.get("id") or f"kb-{uuid.uuid4().hex[:12]}",
                    source=src)
                s.add(obj)
            for k in ("process_type", "title", "equipment", "material",
                      "parameters", "results", "reliability_score",
                      "constraints", "tags"):
                if k in entry:
                    setattr(obj, k, entry[k])
            s.commit()
            s.refresh(obj)
            return obj, created

    def set_reliability(self, entry_id: str, score: int) -> bool:
        with self.Session() as s:
            obj = s.get(KnowledgeEntry, entry_id)
            if not obj:
                return False
            obj.reliability_score = int(score)
            s.commit()
            return True

    # ---- 读 ----
    def list(self, process_type: str | None = None, material: str | None = None,
             min_reliability: int | None = None, q: str | None = None,
             limit: int = 200) -> list[dict]:
        with self.Session() as s:
            stmt = select(KnowledgeEntry)
            if process_type:
                stmt = stmt.where(KnowledgeEntry.process_type == process_type)
            if min_reliability is not None:
                stmt = stmt.where(KnowledgeEntry.reliability_score >= min_reliability)
            if q:
                like = f"%{q}%"
                stmt = stmt.where((KnowledgeEntry.title.like(like))
                                  | (KnowledgeEntry.source.like(like)))
            objs = s.scalars(stmt.order_by(
                KnowledgeEntry.reliability_score.desc(),
                KnowledgeEntry.updated_at.desc()).limit(limit)).all()
            out = [o.to_dict() for o in objs]
        if material:
            out = [e for e in out
                   if material in (e.get("material") or {}).get("material", "")]
        return out

    def stats(self) -> dict:
        with self.Session() as s:
            total = s.scalar(select(func.count(KnowledgeEntry.id)))
            rows = s.execute(select(KnowledgeEntry.process_type,
                                    KnowledgeEntry.reliability_score,
                                    func.count(KnowledgeEntry.id))
                             .group_by(KnowledgeEntry.process_type,
                                       KnowledgeEntry.reliability_score)).all()
        by_type: dict = {}
        for pt, score, n in rows:
            by_type.setdefault(pt, {})[str(score)] = n
        return {"total": total, "by_process_type": by_type}
