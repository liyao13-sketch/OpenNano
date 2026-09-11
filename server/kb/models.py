"""组织记忆:知识条目 SQLAlchemy 模型(带 reliability_score + source)。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeEntry(Base):
    """一条知识 = {工艺类型, 设备, 材料, 参数, 结果, 来源, 可信度, 约束, 标签}。"""
    __tablename__ = "knowledge_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    process_type: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    equipment: Mapped[dict] = mapped_column(JSON, default=dict)
    material: Mapped[dict] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(512), index=True)
    reliability_score: Mapped[int] = mapped_column(Integer, default=4)
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "process_type": self.process_type, "title": self.title,
            "equipment": self.equipment or {}, "material": self.material or {},
            "parameters": self.parameters or {}, "results": self.results or {},
            "source": self.source, "reliability_score": self.reliability_score,
            "constraints": self.constraints or [], "tags": self.tags or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
