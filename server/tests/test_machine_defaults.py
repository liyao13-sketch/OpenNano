"""机台实测默认参数（`kb/machine_defaults.py`）—— 回归网。

背景（2026-09-13 owner）：设备模板的默认值是占位值，"DRIE 甚至和 ICP 是一样的"。
本模块从 core 真实历史推默认值，**按段（chuck/etch/dechuck）分开**，且只认唯一匹配的机台。
"""
from __future__ import annotations

import csv
import json

import pytest

from batch_fixtures import BATCH, ROOT, batch_rows, sample_rows
from conftest import seed_core
from kb.core_vocab import TOOL_DISPLAY, TOOL_ID_SENTINEL

# ---------------------------------------------------------------- 机台号取样（**不写死**）
# 为什么（2026-09-28 · 工单 B2-残C）：真机台号/厂名写进公开仓库＝实验室指纹。
# 口径表本身是**加载**来的（`kb/core_vocab`）：本机 = 真清单，公开 clone / CI = 中性样例
# （`DEMO-*`，两者都 ≥3 条）⇒ 断言的对象是"当前环境登记了哪些机台"，不是某台真机。
_TOOL_IDS = [tid for tid in TOOL_DISPLAY if tid != TOOL_ID_SENTINEL]
assert len(_TOOL_IDS) >= 2, (
    f"口径表里已登记的机台不足 2 台（{len(_TOOL_IDS)} 台）⇒ 本文件要「两台不同机台」，判据无法成立")
#: 两台**不同**机台：`TOOL_ICP` 配单段（ICP），`TOOL_DRIE` 配多段（DRIE）。
#: ⚠️ 换的只是机台号，**坑的语义不变**（"按段分开取默认值" / "唯一才认否则报未匹配"）。
TOOL_ICP, TOOL_DRIE = _TOOL_IDS[0], _TOOL_IDS[1]

RUNS = [
    # ICP：单段（etch），两次，第二次是新值 ⇒ 默认取"同段内出现最多"，并列取最近
    {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "sample_id": ROOT, "stage": "ICP",
     "stage_seq": "3", "date": "2026-09-06", "tool_id": TOOL_ICP},
    {"run_id": f"{BATCH}-ICP-0002", "batch_id": BATCH, "sample_id": ROOT, "stage": "ICP",
     "stage_seq": "3", "date": "2026-09-07", "tool_id": TOOL_ICP},
    # DRIE：**多段**（chuck/etch/dechuck）—— 不许把 dechuck 的值当成刻蚀默认值
    {"run_id": f"{BATCH}-DRIE-0001", "batch_id": BATCH, "sample_id": ROOT, "stage": "DRIE",
     "stage_seq": "5", "date": "2026-09-08", "tool_id": TOOL_DRIE},
]

STEPS = [
    (f"{BATCH}-ICP-0001", 1, "etch", {"phase": "etch", "source_w": 750, "bias_w": 150, "chf3_sccm": 45}),
    (f"{BATCH}-ICP-0002", 1, "etch", {"phase": "etch", "source_w": 780, "bias_w": 220, "chf3_sccm": 45}),
    # chuck ×2 / etch ×2 / dechuck ×1：段内取值不同的键要按"出现最多"选
    (f"{BATCH}-DRIE-0001", 1, "chuck", {"phase": "chuck", "esc_voltage": 800}),
    (f"{BATCH}-DRIE-0001", 2, "chuck", {"phase": "chuck", "esc_voltage": 1000}),
    (f"{BATCH}-DRIE-0001", 3, "etch", {"phase": "etch", "sf6_sccm": 200, "bias_w": 12, "source_w": 600}),
    (f"{BATCH}-DRIE-0001", 4, "etch", {"phase": "etch", "sf6_sccm": 200, "bias_w": 12, "source_w": 600}),
    (f"{BATCH}-DRIE-0001", 5, "dechuck", {"phase": "dechuck", "sf6_sccm": 50, "bias_w": 0}),
]


@pytest.fixture
def core(tmp_path, monkeypatch):
    import pathlib
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", batches=batch_rows(), samples=sample_rows(), runs=RUNS)
    with (d / "steps.csv").open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for rid, order, name, pj in STEPS:
            w.writerow([f"{rid}.S{order:02d}", rid, order, order, name, name,
                        "10", "", "", json.dumps(pj, ensure_ascii=False), ""])
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    return d


def test_按段分开给默认值(core):
    """★ 多段工艺：dechuck 的值**绝不能**当成刻蚀默认值（DRIE 曾出现 bias_w=0）。

    ⚠️ 机台号取自口径表（`TOOL_DRIE`）而非写死；**这条断言仍能抓住**：把各段混成一组、
    让 dechuck 末尾的 `bias_w=0` 覆盖 etch 段的值（`set(g["phases"])`/逐段取值都钉住它）。
    """
    from kb.machine_defaults import machine_defaults
    g = next(g for g in machine_defaults()["groups"] if g["tool_id"] == TOOL_DRIE)
    assert set(g["phases"]) == {"chuck", "etch", "dechuck"}
    etch = g["by_phase"]["etch"]["params"]
    dech = g["by_phase"]["dechuck"]["params"]
    assert etch["bias_w"] == 12 and etch["sf6_sccm"] == 200
    assert dech["bias_w"] == 0                      # 只在 dechuck 段里
    assert g["by_phase"]["chuck"]["params"]["esc_voltage"] in (800, 1000)


def test_单段工艺给扁平参数(core):
    """单段（etch）工艺给**扁平** `params`/`per_key`；`TOOL_ICP` 取自口径表（不写死机台号）。

    **这条断言仍能抓住**：两次 run 值不同时"取该段内出现最多、并列取最近"被写错
    （例如取第一次 / 取最小值）—— 机台号只是夹具，取值规则的检验力不变。
    """
    from kb.machine_defaults import machine_defaults
    g = next(g for g in machine_defaults()["groups"] if g["tool_id"] == TOOL_ICP)
    assert g["phases"] == ["etch"]
    # 两次运行、值不同 ⇒ 取"该段内出现最多"，并列时取最近那次
    assert g["params"]["source_w"] == 780
    assert g["per_key"]["source_w"]["n"] == 2
    assert g["per_key"]["source_w"]["n_distinct"] == 2
    assert (g["per_key"]["source_w"]["min"], g["per_key"]["source_w"]["max"]) == (750, 780)


def test_每个值都能追到来源(core):
    from kb.machine_defaults import machine_defaults
    for g in machine_defaults()["groups"]:
        for ph, blk in g["by_phase"].items():
            for k, meta in blk["per_key"].items():
                assert meta["from_run"], (g["tool_id"], ph, k)
                assert meta["date"], (g["tool_id"], ph, k)


def test_按工序过滤(core):
    """`--stage` 过滤只留该工序；机台号取自口径表（`TOOL_DRIE`）。

    **这条断言仍能抓住**：`stage` 过滤失效（把别的工序的组也带出来）—— 集合相等仍钉住
    "DRIE 只剩那一台"，只是那台的名字不再写死。
    """
    from kb.machine_defaults import machine_defaults
    only = machine_defaults("DRIE")["groups"]
    assert {g["tool_id"] for g in only} == {TOOL_DRIE}
    assert [g["stage"] for g in machine_defaults("ICP")["groups"]] == ["ICP"]


def test_没记机台的_run不参与(core):
    """tool_id 为空的 run 不许被拿去当默认值（不猜）。"""
    from kb.machine_defaults import machine_defaults
    res = machine_defaults()
    assert all(g["tool_id"] for g in res["groups"])


def test_机台匹配_唯一才认否则如实报未匹配():
    """四条匹配分支逐条钉住：① 名字精确 ② 型号精确 ③ 多义不猜 ④ 特征词（≥5 字符）唯一命中。

    ⚠️ 机台档案是**合成的中性样例**（公开仓库零真机台/厂名指纹 · 工单 B2-残C）——
    换的只是名字，**检验力一字不变**：
      · 命中多台（`DEMO-ETCH` 同时像 A1 / A2 / `DEMO-ETCH-BIG`）⇒ 必须 `None`（不许挑一个）；
      · 档案里完全没有 ⇒ `None`（如实报未匹配，而不是硬塞一台）；
      · `DEMO-ALIGNERX-D` 只能靠**特征词**命中型号 `Vendor AlignerX`（前三条分支都不中）。
    """
    from kb.machine_defaults import _match_machine
    ms = [{"id": "1", "name": "DEMO-ETCH-A1", "model": "DEMO-ETCH-A1"},
          {"id": "2", "name": "DEMO-ETCH-A2", "model": "DEMO-ETCH-A2"},
          {"id": "3", "name": "DEMO-DRIE-B", "model": "DEMO-ETCH-BIG"},
          {"id": "4", "name": "DEMO-LITHO-C", "model": "Vendor AlignerX"}]
    assert _match_machine("DEMO-DRIE-B", ms)["id"] == "3"          # ① 名字精确
    assert _match_machine("DEMO-ETCH-BIG", ms)["id"] == "3"        # ② 型号精确（名字不是它）
    assert _match_machine("DEMO-ALIGNERX-D", ms)["id"] == "4"      # ④ 特征词 alignerx 唯一命中
    assert _match_machine("DEMO-ETCH", ms) is None                 # ③ 多义 ⇒ 不猜
    assert _match_machine("DEMO-SPIN-D", ms) is None               # 档案里没有 ⇒ 报未匹配


def test_sentinel_tool_id_is_excluded_and_counted(tmp_path, monkeypatch):
    """哨兵 `tool_id=UNKNOWN`（机台未记录）**不能当机台出处**：那些 run 要排除，且**如实计数**。

    为什么（数据线 2026-09-14 提出）：哨兵组的 run 可能来自**不同机器**，把它们混成一组再推荐
    "这台机器的实测默认值"＝**编归属**。它与本模块既有口径一致（"机台匹配唯一才认/不猜"）。
    """
    import kb.machine_defaults as md
    runs = RUNS + [
        {"run_id": f"{BATCH}-RIE-0001", "batch_id": BATCH, "sample_id": ROOT, "stage": "RIE",
         "stage_seq": "5", "date": "2026-09-09", "tool_id": TOOL_ID_SENTINEL},
    ]
    steps = STEPS + [(f"{BATCH}-RIE-0001", 1, "etch", {"phase": "etch", "source_w": 999})]
    d = seed_core(tmp_path / "core", batches=batch_rows(), samples=sample_rows(), runs=runs)
    with (d / "steps.csv").open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for rid, order, name, pj in steps:
            w.writerow([f"{rid}.S{order:02d}", rid, order, order, name, name,
                        "10", "", "", json.dumps(pj, ensure_ascii=False), ""])
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    monkeypatch.setattr(md, "CORE_DIR", d, raising=False)
    got = md.machine_defaults()
    assert all((g.get("tool_id") or "") != md.TOOL_ID_SENTINEL for g in got["groups"]), \
        "哨兵组仍被当成机台默认值推荐"
    assert got["skipped_unknown_tool"] >= 1, "排除了却没说排除了几条（静默）"


def test_sentinel_value_matches_the_data_line():
    """跨线逐字：我们的 `TOOL_ID_SENTINEL` 必须与数据线 `core_schema.TOOL_ID_SENTINEL` 一致。

    两边都写这个字面量（产品代码不许 import 用户数据目录），所以**必须有判据钉住**，
    否则一边改字面量（比如改成 `unknown`）会静默漂移。只读导入，缺失则跳过。
    """
    import importlib.util
    from conftest import WS_ROOT
    from kb.machine_defaults import TOOL_ID_SENTINEL
    p = WS_ROOT / "个人空间/18_工艺数据资产/03_实验数据/ingest/core_schema.py"
    if not p.exists():
        pytest.skip("工作区里没有数据线的 core_schema.py（评测环境）")
    spec = importlib.util.spec_from_file_location("_core_schema_probe2", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert TOOL_ID_SENTINEL == mod.TOOL_ID_SENTINEL, \
        f"哨兵值漂移：我们 {TOOL_ID_SENTINEL!r} vs 他们 {mod.TOOL_ID_SENTINEL!r}"
