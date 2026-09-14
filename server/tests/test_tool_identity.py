"""机台口径（`tool` / `tool_id`）的**导出侧**契约 —— 回归网（2026-09-14）。

## 来历（数据线真包联调，不采信转述）

他们不手搓夹具（那会给假绿），而是拿**我们的真导出代码**造真包喂 `build_core`：
`kb.expack.build_expack` × 真工程 `AR50-T1-明天.json` + 一个 TEM 节点、edge 接 DRIE-0001。
结果**整包被拦，拦点是机台闸（不是 stage 闸）**：
  ① 检测节点（metrology）导出的 `tool_id` 是**空**；
  ② `PECVD` 模块导出的 `tool_id='PECVD'` —— 那是 **stage 名**；
  ③ 同一个 `tool_id` 在包内出现**两个显示名**（画布写模板名 `Plasma Strip`，core 写 `SAMCO RIE10NR（氟基）`）。
根因：导出侧只认画布字段 `machine_name`（**应用库显示名**：`DRIE-Bosch` / `PECVD` / `ICP-鲁汶`），
从不 round-trip core 的 `tool_id`/`tool`。

⚠️ **比"被闸拦住"更要紧的一件事**：闸只拦**格式**，拦不住**错机台** —— 本文件
`test_probe_like_package_no_longer_silently_misattributes_machines` 记录了实测：
`tool_id='DRIE-Bosch'`（真值 `RIE-400iPB`）与 `tool_id='ICP-鲁汶'`（真值 `ICP-PishowA`）
**一条闸都不报**，会静默入库把机台归属记错 ⇒ 修的是**口径**，不只是"让闸放行"。

## 修法（方案 A：往返 + 哨兵，不做别名表）

画布模块带 `core_tool_id` / `core_tool`（与 `core_run_id` / `core_recipe_id` 同一套往返），
导出走 `kb/core_vocab.resolve_tool`：core 原值 → 应用库机台档案的 `tool_id` → 哨兵 `UNKNOWN`。
**绝不做"画布名 → core tool_id"的别名表**：那是猜（数据线明确否掉了方案 B ——
`PECVD` 的 model 为空，推不出该映到 `PECVD-SAMCO`）。

本文件的判据与数据线 `core_schema.validate_tool_ids` 的四类错误**一一对应**
（①空 / ②stage 名 / ③一名多写 / ④缺显示名）—— 他们拦，我们自查。
"""
from __future__ import annotations

import csv
import importlib.util
import io
import zipfile

import pytest

from conftest import WS_ROOT, seed_core
from batch_fixtures import BATCH as CORE_BATCH
from batch_fixtures import batch_rows, run_rows, sample_rows

from kb.append_pack import build_append_pack
from kb.core_vocab import (TOOL_DISPLAY, TOOL_ID_SENTINEL, TOOL_UNKNOWN_DISPLAY,
                           resolve_tool)
from kb.expack import STAGE_CODES, build_expack, extract_rows, parse_expack

BATCH = "TID-T1"
TOOL_COL, TOOL_ID_COL = 8, 9            # runs.csv 列序（见 expack.build_expack 的表头）

TEM_TMPL = "透射电镜（TEM）"
SEM_TMPL = "扫描电镜（SEM）"


def _mod(mid, eq, **kw):
    m = {"id": mid, "equipment_name": eq, "params": {}, "param_outputs": []}
    m.update(kw)
    return m


class _Lib:
    """最小 lib 桩：机台档案**带 `tool_id`**（core 口径）—— 这正是过去导出侧没读的那个字段。

    三个机台各有各的坑：`PECVD` 的显示名恰好是 stage 名；`DRIE-Bosch` 只是显示名；
    `ICP-Sentech` 在库里的 `tool_id` 是空的（真值未知 ⇒ 必须落哨兵，不许编）。
    """

    def __init__(self):
        self.data = {
            "machines": [
                {"id": "mc_pecvd", "name": "PECVD", "tool_id": "PECVD-SAMCO", "equipment_id": "e1"},
                {"id": "mc_drie", "name": "DRIE-Bosch", "tool_id": "RIE-400iPB", "equipment_id": "e2"},
                {"id": "mc_rie10", "name": "RIE10NR", "tool_id": "RIE10NR", "equipment_id": "e3"},
                {"id": "mc_sentech", "name": "ICP-Sentech", "tool_id": None},
            ],
            "equipment": {"deposition": [{"id": "e1", "name": "PECVD"}],
                          "etch": [{"id": "e2", "name": "DRIE (Bosch)"},
                                   {"id": "e3", "name": "Plasma Strip"}]},
        }

    def machines(self):
        return list(self.data["machines"])


def _runs(proj, lib=None):
    runs, *_ = extract_rows(proj, lib=lib)
    return runs


def _gate(rows):
    """复刻数据线 `core_schema.validate_tool_ids` 的四类错误（我们这侧也要拦得住）。"""
    seen: dict[str, set] = {}
    errs: list[str] = []
    for r in rows:
        rid = r[0]
        tool = str(r[TOOL_COL] or "").strip()
        tid = str(r[TOOL_ID_COL] or "").strip()
        if not tid:
            errs.append(f"① {rid}: tool_id 为空（不知道就写 `{TOOL_ID_SENTINEL}`，别留空）")
            continue
        if tid in STAGE_CODES:
            errs.append(f"② {rid}: tool_id=`{tid}` 是 stage 名，不是机台号")
        if not tool:
            errs.append(f"④ {rid}: tool_id=`{tid}` 但没有 tool 显示名")
        seen.setdefault(tid, set()).add(tool)
    for tid, names in seen.items():
        if len(names) > 1:
            errs.append(f"③ tool_id=`{tid}` 有 {len(names)} 个显示名：{sorted(names)}")
    return errs


# ---------------------------------------------------------------- ② stage 名不是机台号

def test_library_machine_name_is_never_written_as_tool_id():
    """**决定性用例（数据线的 ②）**：画布机台显示名 `PECVD` 恰好是 core 的 **stage 名**，
    导出必须写机台档案里的 core 口径 `PECVD-SAMCO` + 唯一显示名。"""
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "PECVD", machine_id="mc_pecvd", machine_name="PECVD")]}
    row = _runs(proj, _Lib())[0]
    assert row[TOOL_ID_COL] == "PECVD-SAMCO"
    assert row[TOOL_COL] == TOOL_DISPLAY["PECVD-SAMCO"]
    assert _gate([row]) == []


def test_display_name_never_leaks_into_tool_id_even_without_a_library():
    """没有 lib（评测/命令行环境）：**宁可写哨兵，也不写一个看着像机台号的显示名**。

    `DRIE-Bosch` 的真值是 `RIE-400iPB`；写显示名会被数据线机台闸**放过**（它不是 stage 名、
    也不重复）⇒ 静默把机台归属记错。这是本文件里最要紧的一条：**闸放行 ≠ 口径正确**。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "DRIE (Bosch)", machine_name="DRIE-Bosch")]}
    row = _runs(proj)[0]
    assert row[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert row[TOOL_ID_COL] != "DRIE-Bosch"
    assert row[TOOL_COL] == TOOL_UNKNOWN_DISPLAY
    assert _gate([row]) == []


# ---------------------------------------------------------------- ① 检测节点：不留空

def test_metrology_node_without_a_machine_gets_the_sentinel_not_empty():
    """**数据线的 ①**：TEM 检测节点没有机台 ⇒ 写哨兵，**不许留空**
    （空的语义是"漏填"，与"机台未记录/尚未定"必须分得开）。"""
    proj = {"name": BATCH, "edges": [{"src": "m1", "dst": "m2"}],
            "modules": [_mod("m1", "PECVD"), _mod("m2", TEM_TMPL, subtype="tem")]}
    rows = _runs(proj)
    tem = next(r for r in rows if r[3] == "TEM")
    assert tem[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert tem[TOOL_COL] == TOOL_UNKNOWN_DISPLAY
    assert _gate(rows) == []


def test_library_machine_without_a_tool_id_falls_back_to_the_sentinel():
    """库里机台档案**自己没填 `tool_id`**（真值未知）⇒ 哨兵。**绝不拿显示名顶上。**"""
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "ICP Etch", machine_id="mc_sentech",
                             machine_name="ICP-Sentech")]}
    row = _runs(proj, _Lib())[0]
    assert row[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert _gate([row]) == []


# ---------------------------------------------------------------- 往返（导入 → 导出）

def test_core_tool_identity_round_trips_from_a_package(tmp_path):
    """**往返**：包（无 flow.json，即 core 镜像/手工采集包）→ 画布 → 再导出，机台口径逐字不变。

    修前：导入只留 `machine_name`（库内显示名），再导出就换成显示名 ⇒ 归属漂了。
    """
    head = ("run_id,batch_id,sample_id,stage,stage_seq,date,tool,tool_id,parent_run_id\n"
            f"{BATCH}-PECVD-0001,{BATCH},,PECVD,1,2026-09-01,"
            f"SAMCO PD-220NL（PECVD）,PECVD-SAMCO,\n"
            f"{BATCH}-DRIE-0001,{BATCH},,DRIE,2,2026-09-02,"
            f"SAMCO RIE-400iPB（Bosch）,RIE-400iPB,{BATCH}-PECVD-0001\n")
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(f"{BATCH}/runs.csv", head.encode())
        zf.writestr(f"{BATCH}/manifest.json", b'{"batch_id": "TID-T1"}')
    pkg = tmp_path / f"{BATCH}.zip"
    pkg.write_bytes(z.getvalue())
    proj = parse_expack(pkg, None)
    assert [m.get("core_tool_id") for m in proj["modules"]] == ["PECVD-SAMCO", "RIE-400iPB"]
    rows = _runs(proj)
    got = {r[0]: (r[TOOL_COL], r[TOOL_ID_COL]) for r in rows}
    assert got[f"{BATCH}-PECVD-0001"] == ("SAMCO PD-220NL（PECVD）", "PECVD-SAMCO")
    assert got[f"{BATCH}-DRIE-0001"] == ("SAMCO RIE-400iPB（Bosch）", "RIE-400iPB")
    assert _gate(rows) == []


def test_imported_core_identity_wins_over_a_canvas_side_machine():
    """已入库的 run：机台就是 **core 里那台**，画布上改机台**不改记录**（core 原值优先）。

    理由：`core_run_id` 在 ⇒ 这条 run 在 core 里已有归属，导出只是把记录再带一遍。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "PECVD", machine_id="mc_pecvd", machine_name="PECVD",
                             core_run_id=f"{BATCH}-PECVD-0001",
                             core_tool_id="RIE10NR", core_tool="SAMCO RIE10NR（氟基）")]}
    row = _runs(proj, _Lib())[0]
    assert (row[TOOL_COL], row[TOOL_ID_COL]) == ("SAMCO RIE10NR（氟基）", "RIE10NR")


# ---------------------------------------------------------------- ③ 一名一写

def test_one_tool_id_has_exactly_one_display_name_in_a_package():
    """**数据线的 ③**：同一台机（`RIE10NR`）上的"导入行"与"新节点"必须**同一个显示名**。

    修前：新节点写画布模板名（`RIE` / `Plasma Strip`），导入行写 core 显示名 ⇒ ③ 类错误。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [
                _mod("m1", "Plasma Strip", machine_id="mc_rie10", machine_name="RIE10NR"),
                _mod("m2", "RIE", core_run_id=f"{BATCH}-RIE-0001",
                     core_tool_id="RIE10NR", core_tool=TOOL_DISPLAY["RIE10NR"]),
            ]}
    rows = _runs(proj, _Lib())
    names = {r[TOOL_COL] for r in rows if r[TOOL_ID_COL] == "RIE10NR"}
    assert names == {TOOL_DISPLAY["RIE10NR"]}
    assert _gate(rows) == []


def test_probe_like_package_no_longer_silently_misattributes_machines():
    """**真工程形状**的包（PECVD / DWL66 / RIE10NR / DRIE / TEM）过四类闸 + 机台号全对。

    这一条同时是"错机台"的回归锁：修前 `DRIE-Bosch` / `ICP-鲁汶` 这类显示名会被闸**放过**
    （见 `test_display_name_never_leaks_into_tool_id_even_without_a_library`），只能靠断言真值守住。
    """
    proj = {"name": BATCH, "edges": [{"src": "m5", "dst": "m6"}],
            "modules": [
                _mod("m1", "PECVD", machine_id="mc_pecvd", machine_name="PECVD"),
                _mod("m2", "Laser Direct Write", machine_name="DWL66"),   # 画布只有显示名
                _mod("m3", "Plasma Strip", machine_id="mc_rie10", machine_name="RIE10NR"),
                _mod("m4", "DRIE (Bosch)", machine_id="mc_drie", machine_name="DRIE-Bosch"),
                _mod("m5", "ICP Etch", machine_id="mc_sentech", machine_name="ICP-Sentech"),
                _mod("m6", TEM_TMPL, subtype="tem"),
            ]}
    rows = _runs(proj, _Lib())
    assert _gate(rows) == []
    got = {r[3]: r[TOOL_ID_COL] for r in rows}
    assert got["PECVD"] == "PECVD-SAMCO"
    assert got["DRIE"] == "RIE-400iPB"          # 不是 `DRIE-Bosch`
    assert got["ASH"] == "RIE10NR"
    assert got["ICP"] == TOOL_ID_SENTINEL       # 库里没填 tool_id ⇒ 哨兵，不编归属
    assert got["LDW"] == TOOL_ID_SENTINEL       # 这台机不在库桩里 ⇒ 哨兵（不拿显示名 `DWL66` 顶）
    assert got["TEM"] == TOOL_ID_SENTINEL


# ---------------------------------------------------------------- 追加包走同一处口径

@pytest.fixture
def core(tmp_path, monkeypatch):
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", runs=run_rows(), batches=batch_rows(),
                  samples=sample_rows())
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    return d


def test_append_pack_uses_the_same_tool_resolution(core):
    """追加包与整包**同一处解析**：只改 `expack` 会漏掉这条路（同一个包的两个出口）。"""
    proj = {"name": "追加工程", "edges": [],
            "modules": [{"id": "m1", "name": "续做", "equipment_name": "DRIE (Bosch)",
                         "core_run_id": f"{CORE_BATCH}-DRIE-0009", "core_date": "2026-09-20",
                         "machine_id": "mc_drie", "machine_name": "DRIE-Bosch",
                         "params": {}, "param_outputs": []}]}
    blob, info = build_append_pack(proj, lib=_Lib())
    assert info["ok"], info
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if n.endswith("runs.csv"))
    rows = list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))
    assert rows[0]["tool_id"] == "RIE-400iPB"
    assert rows[0]["tool"] == TOOL_DISPLAY["RIE-400iPB"]


# ---------------------------------------------------------------- 【跨线】逐字一致

def test_cross_line_tool_display_is_byte_identical():
    """**跨线逐字判据**：机台表 / 哨兵 / 显示名 —— 我们这侧的镜像与数据线 `core_schema.py` 逐字一致。

    ⚠️ 只读数据线的文件（`18_工艺数据资产/` 对我们只读）：缺失则跳过（评测/CI 环境没有它）。
    """
    p = WS_ROOT / "个人空间/18_工艺数据资产/03_实验数据/ingest/core_schema.py"
    if not p.exists():
        pytest.skip("工作区里没有数据线的 core_schema.py（评测环境）")
    spec = importlib.util.spec_from_file_location("_core_schema_probe_tool", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                                  # 只读导入，不写任何东西
    assert tuple(TOOL_DISPLAY.items()) == tuple(mod.TOOL_DISPLAY.items()), (
        f"机台表不一致（顺序也算）：\n  我们 {tuple(TOOL_DISPLAY.items())}\n"
        f"  他们 {tuple(mod.TOOL_DISPLAY.items())}")
    assert TOOL_ID_SENTINEL == mod.TOOL_ID_SENTINEL
    assert TOOL_UNKNOWN_DISPLAY == mod.TOOL_UNKNOWN_DISPLAY
    # stage 词表：`STAGE_CODES` 是我们写 `tool_id` 时用来"挡 stage 名"的那张表 ⇒ 必须等于他们的 STAGES
    assert set(STAGE_CODES) == set(mod.STAGES), "我们的 stage 集合与 core_schema.STAGES 不一致"


# ---------------------------------------------------------------- 端到端：真包

def test_exported_package_passes_the_four_gates_end_to_end():
    """E2E：**走真导出函数**（`build_expack`）出 zip → 解包读 runs.csv → 四类闸全过。

    单元层用 `extract_rows` 快，但真正被数据线喂给 `build_core` 的是 `build_expack` 的产物；
    这一条保证"CSV 列序 / 表头 / zip 里的那份"与判据看到的是同一份东西。
    """
    proj = {"name": BATCH, "edges": [{"src": "m4", "dst": "m5"}],
            "modules": [_mod("m1", "PECVD", machine_id="mc_pecvd", machine_name="PECVD"),
                        _mod("m4", "DRIE (Bosch)", machine_id="mc_drie", machine_name="DRIE-Bosch"),
                        _mod("m5", TEM_TMPL, subtype="tem")]}
    data, batch = build_expack(proj, lib=_Lib())
    z = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in z.namelist() if n.endswith("runs.csv"))
    rows = list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))
    assert rows, "包里没有 run 行"
    assert [r["run_id"] for r in rows] == [f"{batch}-PECVD-0001", f"{batch}-DRIE-0001",
                                           f"{batch}-TEM-0001"]
    assert [r["tool_id"] for r in rows] == ["PECVD-SAMCO", "RIE-400iPB", TOOL_ID_SENTINEL]
    assert [r["tool"] for r in rows] == [TOOL_DISPLAY["PECVD-SAMCO"],
                                         TOOL_DISPLAY["RIE-400iPB"], TOOL_UNKNOWN_DISPLAY]


# ---------------------------------------------------------------- 解析器本身

def test_resolve_tool_prefers_id_over_a_stale_display_name():
    """机台档案按 **id** 认（id 是我们自己发的，最硬）；名字只是回退，且**认不到就哨兵**。"""
    lib = _Lib()
    tid, name = resolve_tool({"machine_id": "mc_drie", "machine_name": "写错了的名字"},
                             lib.machines(), STAGE_CODES)
    assert (tid, name) == ("RIE-400iPB", TOOL_DISPLAY["RIE-400iPB"])
    tid2, name2 = resolve_tool({"core_tool_id": "SEM"}, lib.machines(), STAGE_CODES)
    assert (tid2, name2) == (TOOL_ID_SENTINEL, TOOL_UNKNOWN_DISPLAY)   # ② 兜底：stage 名绝不入 tool_id
