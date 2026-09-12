"""知识库存取:SQLite + 按 (source, locator) 幂等 upsert + 过滤检索。

v0.2(2026-09-12,跨线定案) 三处改动:
1. 启动时幂等迁移:缺 `extra_metadata` 列则 ALTER TABLE 补上(老库无需重建)。
2. upsert 幂等键由「只按 source」改为「(source, locator)」——否则同一文献
   (source 相同、loc 不同)的多条知识会互相覆盖。
3. `resolve_reliability()` 来源分档守卫:非 core 来源缺省不再冒充 4 分,
   一律落 2 并写 reliability_basis 留痕。契约见
   `19_工艺资料/契约/知识条目Schema与录入规范_v0.2_20260912.md` §二。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker

from opennano_config import DB_PATH as _DB_PATH

from .models import Base, KnowledgeEntry

#: 路径的**唯一来源**在 `opennano_config`（可用 `OPENNANO_DB` 覆盖；测试指向临时库）
DEFAULT_DB = _DB_PATH

#: 来源分档 → 允许的可靠度取值区间(闭区间)
SOURCE_TIER_RULES = {
    "core":        (1, 5),   # 本实验室实测:走 derive_reliability 自动派生
    "public_data": (2, 3),   # 公开一手数据(文献表/数据集):4 需 >=2 独立源互证
    "literature":  (2, 4),   # 文献机理/结论:2 未验证 / 4 权威源互证
    "manual":      (1, 2),   # 手册、出厂参考值
    "legacy":      (1, 5),   # 历史行:不追溯
}
#: 档位内的**离散允许分**(规范 §2.2):文献档 2 或 4(1=已被推翻/与设备冲突)
#: 存在离散表时以离散表为准 —— 区间只兜底未列出的档
TIER_ALLOWED_SCORES = {
    "literature": (1, 2, 4),
    "public_data": (2, 3),
    "manual": (1, 2),
}
DEFAULT_TIER = "legacy"
DEFAULT_SCORE = 2


def locator_of(entry: dict) -> str:
    """幂等键的定位符:extra_metadata.locator -> loc -> id -> ''。"""
    md = entry.get("extra_metadata") or {}
    return str(md.get("locator") or md.get("loc") or entry.get("id") or "")


def tier_of(entry: dict) -> str | None:
    """来源分档:显式 extra_metadata.source_tier > 由 source 猜 > None。"""
    md = entry.get("extra_metadata") or {}
    explicit = md.get("source_tier")
    if explicit:
        return str(explicit)
    src = str(entry.get("source") or "")
    if src.startswith(("18_工艺数据资产", "core", "core/")):
        return "core"
    if src.startswith(("D", "T", "LIT_")) and "/" not in src:
        return None  # 形如 D29 ... 的文献来源,交由调用方显式标注
    return None


def _has_core_run(entry: dict) -> bool:
    for c in entry.get("constraints") or []:
        if isinstance(c, dict) and c.get("type") == "core_run":
            return True
    return False


def extract_basis(entry: dict) -> dict:
    """从 entry(含本次 anchor 或库内行)里提取"可继承的分档依据"。

    含 `reliability_basis_provenance` 时以它定权:
      - `manual`/`anchor` ⇒ 人工裁决/升锚依据,守卫**默认文案不得覆盖**
      - `guard`          ⇒ 守卫派生文案,下一轮的守卫文案可以更新它
    缺 provenance 时按内容猜(`tier=`/`缺省降级`/`超区间降级`/`档位不符降级`/`core` 开头视为守卫产物)。
    """
    md = entry.get("extra_metadata") or {}
    basis = md.get("reliability_basis")
    if not basis:
        return {}
    prov = md.get("reliability_basis_provenance")
    if not prov:
        b = str(basis)
        prov = ("guard" if ("tier=" in b or b.startswith(
            ("缺省降级", "超区间降级", "档位不符降级", "core 来源未传分"))) else "manual")
    return {"basis": basis, "provenance": prov, "score": entry.get("reliability_score")}


def _guard_write(md: dict, text: str, prev: dict | None, score_changed: bool = True) -> None:
    """写守卫文案。

    若库内是**人给/升锚依据**且**本次分未变** ⇒ 保留库内依据,只记 guard_note;
    若分确实变了(升/降级) ⇒ 旧依据已过时,写新守卫文案为 authoritative reason,
    但把旧依据附在 `prior_basis` 里留档(不丢信息)。
    """
    if prev and prev.get("provenance") in ("manual", "anchor") and not score_changed:
        hist = list(md.get("guard_note_history") or [])
        hist.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "text": text, "kept_basis": prev["basis"]})
        md["guard_note"] = text
        md["guard_note_history"] = hist[-10:]
        md["reliability_basis"] = prev["basis"]          # 沿用,不覆盖
        md["reliability_basis_provenance"] = prev["provenance"]
        return
    if prev and prev.get("provenance") in ("manual", "anchor"):
        md["prior_basis"] = prev["basis"]                # 分变了:旧依据留档
    md["reliability_basis"] = text
    md["reliability_basis_provenance"] = "guard"


def resolve_reliability(entry: dict, stored: dict | None = None) -> int:
    """决定写库用的 reliability_score,并把依据写进 extra_metadata.reliability_basis。

    - core 来源:显式传分优先(ingest.py 的派生值);没传才兜底 2。
    - 其它来源:显式传分必须落在**该档允许值**内(有离散表用离散表,否则用区间);
      缺省落 2 并留痕。文献档只认 {1,2,4} —— 3 分在规范里没有定义,不许写。
    - **依据保真(2026-09-12 增补)**:库内已有可靠依据(人工裁决/升锚/守卫)且分未被本次改变
      ⇒ **沿用库内 `reliability_basis`**,不被守卫默认文案洗掉;确需记录的守卫文案进 `guard_note`。
    """
    md = dict(entry.get("extra_metadata") or {})
    tier = tier_of(entry) or DEFAULT_TIER
    if _has_core_run(entry):
        tier = "core"
    md["source_tier"] = tier
    incoming = extract_basis(entry)                 # 本次锚表/守卫自己带来的依据
    stored_basis = extract_basis(stored) if stored else None

    # 库内依据带入:md 是从**解析结果**重建的,不带入就会每轮丢失继承来的依据
    # (上一轮写进库的 basis/provenance/prior/guard_note 必须在场,否则保真逻辑失效)
    if stored_basis and "reliability_basis" not in md:
        md["reliability_basis"] = stored_basis["basis"]
        md["reliability_basis_provenance"] = stored_basis["provenance"]
    for k in ("prior_basis", "guard_note", "guard_note_history"):
        if k not in md and (stored or {}).get("extra_metadata", {}).get(k):
            md[k] = stored["extra_metadata"][k]

    allowed = TIER_ALLOWED_SCORES.get(tier)
    lo, hi = SOURCE_TIER_RULES.get(tier, SOURCE_TIER_RULES["legacy"])

    def carry_prev():
        """分未变时:可继承的库内依据优先于守卫默认文案(不能把升锚理由洗掉)。"""
        if stored_basis and (not incoming or incoming["provenance"] == "guard"):
            md["reliability_basis"] = stored_basis["basis"]
            md["reliability_basis_provenance"] = stored_basis["provenance"]

    def carry_prior():
        """分变了但没有新依据可写时:至少把旧依据留档到 prior_basis(不留空、不改写)。"""
        if stored_basis and incoming and incoming["provenance"] == "anchor":
            if "reliability_basis" not in incoming:
                md.setdefault("prior_basis", stored_basis["basis"])
        elif stored_basis and not incoming:
            md["prior_basis"] = stored_basis["basis"]

    def changed(score: int) -> bool:
        """本次写库后分数是否与库内现值不同(不同 ⇒ 旧依据已过时)。"""
        return bool(stored_basis and stored_basis.get("score") != score)

    def persist_inherited():
        """继承来的依据必须写回 entry.extra_metadata,否则下一轮又变回"库内无依据"。"""
        for k in ("reliability_basis", "reliability_basis_provenance",
                  "prior_basis", "guard_note"):
            v = md.get(k) or (stored_basis or {}).get(k)
            if v:
                md[k] = v

    raw = entry.get("reliability_score")
    if raw is None:
        # §6.5 带依据保护:库内分有可用依据(人工/升锚)且本次没传分 ⇒ 沿用库内分,
        # 不因"这次没给锚表"而静默降级(降级只走显式路径)。
        if (stored_basis and stored_basis.get("provenance") in ("manual", "anchor")
                and stored_basis.get("score") is not None):
            md["reliability_basis"] = stored_basis["basis"]
            md["reliability_basis_provenance"] = stored_basis["provenance"]
            md["guard_note"] = "本次未传分,沿用库内带依据的分(§6.5 带依据保护)"
            entry["extra_metadata"] = md
            return int(stored_basis["score"])
        text = ("core 来源未传分,按未核实兜底 2" if tier == "core"
                else f"缺省降级(来源未传分, tier={tier})")
        _guard_write(md, text, stored_basis, changed(DEFAULT_SCORE))
        if not changed(DEFAULT_SCORE):
            carry_prev()
        carry_prior()
        persist_inherited()
        entry["extra_metadata"] = md
        return DEFAULT_SCORE

    score = int(raw)
    if allowed is not None:
        if score not in allowed:
            snap = DEFAULT_SCORE if DEFAULT_SCORE in allowed else allowed[0]
            _guard_write(md, f"档位不符降级:传 {score} 不在 tier={tier} 允许档 {list(allowed)}",
                         stored_basis, changed(snap))
            score = snap
    elif score < lo:
        _guard_write(md, f"超区间降级:传 {score} 低于 tier={tier} 下限 {lo}",
                     stored_basis, changed(lo))
        score = lo
    elif score > hi:
        _guard_write(md, f"超区间降级:传 {score} 高于 tier={tier} 上限 {hi}"
                         f"(该档需{'互证/人工裁决' if tier != 'core' else 'core 核实'})",
                     stored_basis, changed(hi))
        score = hi
    else:
        if not changed(score):
            carry_prev()                                # 档位内直接放行,分未变 ⇒ 保真
    carry_prior()
    persist_inherited()
    entry["extra_metadata"] = md
    return score


def _norm(v):
    """对比归一化:字典按 key 排序;列表**先排序**(tags 等无序,顺序差异不算漂移)。"""
    if isinstance(v, dict):
        return {k: _norm(v[k]) for k in sorted(v)}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _trunc(v, n: int = 120) -> str:
    if isinstance(v, (dict, list)):
        v = _norm(v)
        if isinstance(v, list):
            v = sorted(v, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)
    return s if len(s) <= n else s[:n] + "…"


#: 重跑漂移需留痕的字段(规范 §四之六)。extra_metadata 自身的版本/升锚字段不算漂移。
DRIFT_FIELDS = ("process_type", "title", "source", "parameters",
                "equipment", "material", "constraints", "tags")


def detect_drift(stored: dict | None, new: dict) -> dict:
    """比较库内值与新解析值。返回 {字段: {old, new}};无差异则 {}。

    不做任何改写 —— 是否采用新值、是否改路由由调用方决定(规范 §四之六)。
    """
    if not stored:
        return {}
    drift = {}
    for f in DRIFT_FIELDS:
        a, b = stored.get(f), new.get(f)
        if _trunc(a) != _trunc(b):
            drift[f] = {"old": _trunc(a), "new": _trunc(b)}
    return drift


def note_drift(entry: dict, drift: dict, changed: list[str] | None = None) -> dict:
    """把差异写进 entry['extra_metadata']['field_drift'](默认保留历史 + 追加本次)。"""
    md = dict(entry.get("extra_metadata") or {})
    hist = list(md.get("field_drift_history") or [])
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "fields": sorted(drift.keys()),
           "applied": sorted(changed or []),
           "detail": drift}
    hist.append(rec)
    md["field_drift"] = rec
    md["field_drift_history"] = hist[-10:]     # 只留最近 10 次,防无界增长
    entry["extra_metadata"] = md
    return entry


class KBStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        self._migrate()
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._backfill_basis_provenance()

    def _migrate(self) -> None:
        """v0.2:老库缺列则补 -- 无 alembic,靠 PRAGMA 幂等检查。"""
        with self.engine.begin() as conn:
            cols = {r[1] for r in conn.execute(
                text("PRAGMA table_info(knowledge_entries)")).fetchall()}
            if cols and "extra_metadata" not in cols:
                conn.execute(text(
                    "ALTER TABLE knowledge_entries ADD COLUMN extra_metadata JSON"))

    def _backfill_basis_provenance(self) -> None:
        """回填 `reliability_basis_provenance`(幂等):已有 basis 但无 provenance 的行,
        按文案判守卫产物(guard)还是人给/升锚依据(manual)。

        否则老行的**人工依据**会在下次重跑时被守卫默认文案覆盖(2026-09-12 工艺线实测)。
        """
        guard_marks = ("缺省降级", "超区间降级", "档位不符降级", "core 来源未传分")
        with self.Session() as s:
            rows = s.scalars(select(KnowledgeEntry).where(
                func.json_extract(KnowledgeEntry.extra_metadata, "$.reliability_basis").isnot(None)
            )).all()
            n = 0
            for obj in rows:
                md = dict(obj.extra_metadata or {})
                if md.get("reliability_basis_provenance"):
                    continue
                b = str(md.get("reliability_basis") or "")
                prov = "guard" if ("tier=" in b or b.startswith(guard_marks)) else "manual"
                md["reliability_basis_provenance"] = prov
                obj.extra_metadata = md
                n += 1
            if n:
                s.commit()
                print(f"[kb] 回填 reliability_basis_provenance: {n} 行")

    # ---- 写 ----
    def fetch(self, source: str, locator: str | None = None,
              entry_id: str | None = None) -> dict | None:
        """读一条库内现值(按 (source, locator);locator 省略时按 id 兜底)。"""
        if locator is None and entry_id:
            locator = ""
        with self.Session() as s:
            obj = s.scalar(select(KnowledgeEntry).where(
                KnowledgeEntry.source == source,
                func.coalesce(func.json_extract(KnowledgeEntry.extra_metadata, "$.locator"),
                              func.json_extract(KnowledgeEntry.extra_metadata, "$.loc"),
                              "").__eq__(locator or "")))
            if obj is None and entry_id:
                obj = s.get(KnowledgeEntry, entry_id)
            return obj.to_dict() if obj else None

    def upsert(self, entry: dict) -> tuple[KnowledgeEntry, bool]:
        """按 (source, locator) 幂等写入。返回 (entry, created)。"""
        entry = dict(entry)
        loc = locator_of(entry)
        with self.Session() as s:
            src = entry.get("source", "")
            obj = s.scalar(select(KnowledgeEntry).where(
                KnowledgeEntry.source == src,
                func.coalesce(func.json_extract(KnowledgeEntry.extra_metadata, "$.locator"),
                              func.json_extract(KnowledgeEntry.extra_metadata, "$.loc"),
                              "").__eq__(loc)))
            created = obj is None
            # 先取库内现值再定分:守卫据此判断"分未变则沿用库内依据"(规范 §四之六)
            stored = obj.to_dict() if obj is not None else None
            score = resolve_reliability(entry, stored)
            if obj is None:
                obj = KnowledgeEntry(
                    id=entry.get("id") or f"kb-{uuid.uuid4().hex[:12]}",
                    source=src)
                s.add(obj)
            # 显式赋值:避免把 None 写进 NOT NULL 列。id 只在新建时定(改主键会触发 UNIQUE 冲突)
            for k in ("process_type", "title", "equipment", "material",
                      "parameters", "results", "source", "constraints", "tags",
                      "extra_metadata"):
                if k in entry and entry[k] is not None:
                    setattr(obj, k, entry[k])
            obj.reliability_score = score
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
             limit: int = 200, include_theory: bool = True) -> list[dict]:
        """检索。include_theory=True 时,按设备过滤也带上 THEORY_*(普适机理)。"""
        with self.Session() as s:
            stmt = select(KnowledgeEntry)
            if process_type:
                if include_theory:
                    stmt = stmt.where(
                        (KnowledgeEntry.process_type == process_type)
                        | KnowledgeEntry.process_type.like("THEORY\\_%", escape="\\"))
                else:
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
