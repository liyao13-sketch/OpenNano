"""手册层 `MANUAL_*` 路由与分档 —— 回归网（2026-09-14，应工艺线跨线工单）。

## 触发点（工艺线实测，不是推测）
resist 说明书试点 3 份 / 36 条喂 `lit_ingest --dry-run`：**21 条被路由到 `THEORY_KINETICS`**
（"前烘 85 °C/90 s"这类**厂商出厂参数**被当成普适机理）。若照此入库，检索侧按设备过滤时
`THEORY_*` 是**默认包含**的（契约 §三 配套规则 1）⇒ 平台会把某厂商的前烘参数推给所有设备，
正是配套规则 3 明令禁止的"把局部结论伪装成普适结论"的**镜像错误**。

## 本文件钉住的口径
1. **判据是"来源"，不是"内容形态"**：说明书件里的条目**全部**落 `MANUAL_*`，
   内容形态留在 `knowledge_type`/tags（实测 36 条的 `knowledge_type` 只有
   「配方窗口/经验数值/事实陈述/参数表/失败警示/trade-off」，**没有一条含"手册"** ⇒
   只看逐条必然漏判）。
2. 判据三级：`--manual` 显式开关 > 抽取件**文件头**声明 > 逐条标记。
3. **文献件一点不许动**：8 份文献抽取件的解析侧分布与改造前逐字一致（本文件用"不得出现
   `MANUAL_*`"这条不变量守住，另有一条合成回归用例）。
4. 分档：`MANUAL_*` → `source_tier="manual"`（契约 §2.1「设备手册/出厂参考值」那行，允许 1–2）。
5. 检索：按设备过滤时 `MANUAL_*` **不搭** `THEORY_*` 的便车。
"""
from __future__ import annotations

import json

import pytest

from kb.lit_ingest import (MANUAL_FALLBACK, build_entry, detect_manual_source,
                           is_manual_item, load_items, pick_manual_subtype, pick_process_type)

#: 一条"内容形态像机理、但来自说明书"的条目 —— 正是被误路由的那一类
ITEM_MECHANISM_LIKE = {
    "id": "M01-K09", "knowledge_type": "经验数值",
    "content": "前烘 85 °C / 90 s；显影 90 s。此为厂商推荐出厂工艺条件。",
    "source": "M01 p.2 §Processing",
    "citation": "`19_工艺资料/光刻曝光/Resist/曝光-手册_AR-N7520负胶_Allresist.pdf` p.2",
    "context": {"设备": "EBL"},
}
ITEM_TABLE_LIKE = {
    "id": "M01-K01", "knowledge_type": "参数表(整表)",
    "content": "三型号整表对照：固含 17/11/7 %；4000 rpm 膜厚 0.4/0.2/0.1 µm。",
    "source": "M01 p.1", "citation": "...pdf p.1", "context": {},
}


def _extract_file(tmp_path, items, header: str):
    p = tmp_path / "X_知识抽取.md"
    p.write_text(header + "\n## 输出\n\n```json\n" + json.dumps(items, ensure_ascii=False) + "\n```\n",
                 encoding="utf-8")
    return p


MANUAL_HEADER = ("# M01 知识抽取（曝光-手册_AR-N7520负胶_Allresist.pdf）\n\n"
                 "> **输入（说明书元信息）**\n> - 产品：AR-N 7520 new\n"
                 "> - 厂商 / 文档：Allresist GmbH，*E-Beam Resists*\n"
                 "> - 用途定位：**出厂参考值 / 预设**，非本室实测\n")
LIT_HEADER = "# D29 知识抽取（某篇论文）\n\n> 来源：Journal of Micromechanics, 2019\n"


# ---------------------------------------------------------------- 判据：来源而非形态

def test_manual_file_header_routes_every_item_to_manual(tmp_path):
    """说明书件里**所有**条目落 MANUAL_* —— 包括那条"像机理"的和那条"像文献整表"的。"""
    p = _extract_file(tmp_path, [ITEM_MECHANISM_LIKE, ITEM_TABLE_LIKE], MANUAL_HEADER)
    from kb.lit_ingest import MANUAL_FILE_MARKERS
    hint = detect_manual_source(p.read_text(encoding="utf-8"))
    assert hint in MANUAL_FILE_MARKERS, f"文件头没被识别：{hint!r}"   # 命中哪个标记不重要
    for it in load_items(p):
        pt = pick_process_type(it, manual_source=True)
        assert pt.startswith("MANUAL_"), f"{it['id']} 落到 {pt}"


def test_same_items_from_a_literature_file_keep_the_old_routing(tmp_path):
    """**同一批条目**换个（文献）文件头 ⇒ 路由回到改造前的行为（这条守住"没误伤文献"）。"""
    p = _extract_file(tmp_path, [ITEM_MECHANISM_LIKE, ITEM_TABLE_LIKE], LIT_HEADER)
    assert detect_manual_source(p.read_text(encoding="utf-8")) == ""
    items = load_items(p)
    assert pick_process_type(items[0]) == "THEORY_KINETICS"     # 经验数值 → 旧行为
    assert pick_process_type(items[1]) == "LIT_TABLE"           # 整表 → 旧行为


def test_item_level_marker_is_enough_without_the_file_header():
    """逐条标注也能认（老抽取件没写文件头时用得上）。"""
    it = dict(ITEM_MECHANISM_LIKE, knowledge_type="厂商说明书")
    assert is_manual_item(it) and pick_process_type(it).startswith("MANUAL_")
    assert not is_manual_item(ITEM_MECHANISM_LIKE)


def test_citation_mentioning_a_manual_must_not_flip_the_layer():
    """**引用了手册 ≠ 自己是手册**：文献条目 citation 里带"手册"两字，仍按文献路由。

    这条是被自己的用例抓出来的真误伤路径（第一版把 citation 也算进判据）：
    一篇论文引用 Allresist 的手册是常态，拿引用当来源判据会把整篇文献误判成手册层。
    """
    lit = {"id": "D9-K01", "knowledge_type": "经验数值",
           "content": "文献给出的前烘条件与手册不同。", "source": "D9 §III (p.12)",
           "citation": "`19_工艺资料/光刻曝光/Resist/曝光-手册_X.pdf` 转引自 D9"}
    assert not is_manual_item(lit)
    assert pick_process_type(lit) == "THEORY_KINETICS"


def test_manual_flag_forces_the_layer_even_without_any_marker():
    """`--manual` 开关是最强判据（文件头没写也能整份归层）。"""
    assert pick_process_type(ITEM_MECHANISM_LIKE, manual_source=True).startswith("MANUAL_")


# ---------------------------------------------------------------- 亚类

@pytest.mark.parametrize("text,expect", [
    ("光刻胶 AR-N 7520 的显影与前烘条件", "MANUAL_LITHO"),
    ("ICP 刻蚀机台的去胶与灰化参数", "MANUAL_ETCH"),
    ("PECVD 沉积二氧化硅的机台参数", "MANUAL_DEPO"),
    ("RCA 清洗与溶剂剥离液配方", "MANUAL_WET"),
    ("退火炉 900 °C 氧化条件", "MANUAL_THERMAL"),
    ("椭偏仪校准与台阶仪量测", "MANUAL_METRO"),
    ("一份没有任何域关键词的零件清单", MANUAL_FALLBACK),      # 显式兜底，不是 GENERAL 式蒙混
])
def test_subtype_by_process_domain(text, expect):
    assert pick_manual_subtype({"content": text}) == expect


def test_subtype_prefers_the_main_domain_of_the_document():
    """光刻胶说明书里顺带提到"耐刻蚀/去胶"时，仍归 LITHO（按主域归架）。"""
    it = {"content": "本胶耐干法刻蚀，可用于去胶工艺；显影 90 s。",
          "citation": "`19_工艺资料/光刻曝光/Resist/曝光-手册_X.pdf`"}
    assert pick_manual_subtype(it) == "MANUAL_LITHO"


# ---------------------------------------------------------------- 分档口径

def test_manual_entries_use_the_manual_tier_and_cap():
    """`source_tier=manual`（契约 §2.1「设备手册/出厂参考值」行，允许 1–2）。"""
    e, _ = build_entry(ITEM_MECHANISM_LIKE, "T-1", "m", manual_source=True)
    md = e["extra_metadata"]
    assert md["source_tier"] == "manual"
    assert md["source_kind"] == "manual_extraction"      # 来源可查，不再混成 literature_extraction
    assert md["manual_doc"]                              # 记下是哪份文档
    from kb.store import TIER_ALLOWED_SCORES
    assert TIER_ALLOWED_SCORES["manual"] == (1, 2)       # 上限 2：出厂标称值不得悄悄升到 3


def test_literature_and_table_tiers_unchanged():
    """文献/文献整表的分档**一点没动**（守住 D29 那 53/3 的现状）。"""
    e_lit, _ = build_entry(ITEM_MECHANISM_LIKE, "T-1", "m")
    e_tab, _ = build_entry(ITEM_TABLE_LIKE, "T-1", "m")
    assert e_lit["extra_metadata"]["source_tier"] == "literature"
    assert e_tab["extra_metadata"]["source_tier"] == "public_data"


# ---------------------------------------------------------------- 检索不搭便车

def _kb(tmp_path):
    from kb.store import KBStore
    return KBStore(tmp_path / "kb.db")


def _entry(iid, ptype, tier="literature", score=2):
    return {"id": iid, "process_type": ptype, "title": iid, "source": "s",
            "material": {}, "equipment": {}, "parameters": {}, "results": {},
            "constraints": [], "tags": [],
            "extra_metadata": {"source_tier": tier, "locator": iid, "reliability_score": score}}


def test_device_filter_includes_theory_but_never_manual(tmp_path):
    """按设备过滤：`THEORY_*` 默认包含（普适机理），`MANUAL_*` **不许**跟着进来。"""
    kb = _kb(tmp_path)
    for e in (_entry("a", "DRIE_Bosch", "public_data", 2),
              _entry("b", "THEORY_KINETICS", "literature", 2),
              _entry("c", "MANUAL_LITHO", "manual", 2),
              _entry("d", "LIT_TABLE", "public_data", 2)):
        kb.upsert(e)
    got = {r["id"] for r in kb.list(process_type="DRIE_Bosch")}
    assert got == {"a", "b"}, f"设备过滤带上了不该带的：{got}"
    got_no = {r["id"] for r in kb.list(process_type="DRIE_Bosch", include_theory=False)}
    assert got_no == {"a"}


def test_manual_entries_are_reachable_by_their_own_filter(tmp_path):
    """MANUAL_* 仍可按自己的层检索（不是被藏起来）。"""
    kb = _kb(tmp_path)
    kb.upsert(_entry("c", "MANUAL_LITHO", "manual", 2))
    assert {r["id"] for r in kb.list(process_type="MANUAL_LITHO")} == {"c"}


# ---------------------------------------------------------------- 真实文件（只读）

def test_real_extraction_files_route_correctly():
    """拿真抽取件跑一遍：M 系列 36/36 落 `MANUAL_*`；8 份文献件**一条都不许**落 `MANUAL_*`。"""
    from conftest import WS_ROOT
    base = WS_ROOT / "个人空间/07_文献库/知识抽取"
    if not base.is_dir():
        pytest.skip("工作区里没有文献抽取目录")          # 评测环境；本机一定有

    m_total = m_manual = 0
    for f in ("M01", "M02", "M04"):
        p = base / f"{f}_知识抽取.md"
        if not p.exists():
            pytest.skip(f"缺 {p.name}")
        items = load_items(p)
        man = bool(detect_manual_source(p.read_text(encoding="utf-8")))
        assert man, f"{p.name} 的说明书标记没被识别"
        m_total += len(items)
        m_manual += sum(1 for i in items if pick_process_type(i, man).startswith("MANUAL_"))
    assert m_total == 36 and m_manual == 36, f"手册路由 {m_manual}/{m_total}（验收要求 36/36）"

    leaked = []
    for p in sorted(base.glob("*_知识抽取*.md")):
        if p.name.startswith("M"):
            continue
        items = load_items(p)
        for i in items:
            if pick_process_type(i, False).startswith("MANUAL_"):
                leaked.append(f"{p.name}:{i.get('id')}")
    assert not leaked, f"文献件被误判成手册：{leaked[:5]}"


# ---------------------------------------------------------------- API 层过滤

def test_api_kb_layer_filter(tmp_path, monkeypatch):
    """`/api/kb?layer=manual` 只出 MANUAL_*；`layer=device` 排除三个前缀层。"""
    import main
    from kb.store import KBStore
    kb = KBStore(tmp_path / "api.db")
    for e in (_entry("a", "DRIE_Bosch", "public_data", 2),
              _entry("b", "THEORY_KINETICS", "literature", 2),
              _entry("c", "MANUAL_LITHO", "manual", 2),
              _entry("d", "LIT_TABLE", "public_data", 2)):
        kb.upsert(e)
    monkeypatch.setattr(main, "KB", kb)
    assert {r["id"] for r in main.api_kb(layer="manual")} == {"c"}
    assert {r["id"] for r in main.api_kb(layer="theory")} == {"b"}
    assert {r["id"] for r in main.api_kb(layer="lit")} == {"d"}
    assert {r["id"] for r in main.api_kb(layer="device")} == {"a"}
