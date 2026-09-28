"""机台口径（`tool` / `tool_id`）的**导出侧**契约 —— 回归网（2026-09-14）。

## 来历（数据线真包联调，不采信转述）

他们不手搓夹具（那会给假绿），而是拿**我们的真导出代码**造真包喂 `build_core`：
`kb.expack.build_expack` × 真工程 `AR50-T1-明天.json` + 一个 TEM 节点、edge 接 DRIE-0001。
结果**整包被拦，拦点是机台闸（不是 stage 闸）**：
  ① 检测节点（metrology）导出的 `tool_id` 是**空**；
  ② `PECVD` 模块导出的 `tool_id='PECVD'` —— 那是 **stage 名**；
  ③ 同一个 `tool_id` 在包内出现**两个显示名**（画布写模板名 `Plasma Strip`，core 写口径表里的权威显示名）。
根因：导出侧只认画布字段 `machine_name`（**应用库显示名**，即库内标签），从不 round-trip core 的
`tool_id`/`tool`。

⚠️ **比"被闸拦住"更要紧的一件事**：闸只拦**格式**，拦不住**错机台** —— 本文件
`test_probe_like_package_no_longer_silently_misattributes_machines` 记录了实测：
库内标签被当成 core 机台号时，机台闸**一条都不报**，会静默入库把机台归属记错
⇒ 修的是**口径**，不只是"让闸放行"。

## 机台号从哪来（2026-09-28 · 去字面量）

公开仓库里**不许出现真机台/厂名**（工单 `20260915-助手线-to-兼-01` B2-残C）。口径表的唯一真相
是数据线的 `core_schema.TOOL_DISPLAY`，工具侧经 `kb/core_vocab.py` **加载**得到：本机 = 真清单
（`mode: enforced`），公开 clone / CI = 仓库内置**中性样例**（`mode: demo`）。

⚠️ **因此本文件的断言一律相对 `TOOL_DISPLAY` 写，绝不写死机台号/厂名字符串**：写死的真名在别的
机器上根本不在表里 ⇒ 会被 `resolve_tool` 判成"未登记"落哨兵，断言随之失真（假红，或更糟的假绿）。
取样入口＝ `REAL` / `tool_at()`（见下）。

## 修法（方案 A：往返 + 哨兵，不做别名表）

画布模块带 `core_tool_id` / `core_tool`（与 `core_run_id` / `core_recipe_id` 同一套往返），
导出走 `kb/core_vocab.resolve_tool`：core 原值 → 应用库机台档案的 `tool_id` → 哨兵 `UNKNOWN`。
**绝不做"画布名 → core tool_id"的别名表**：那是猜（数据线明确否掉了方案 B）。

本文件的判据与数据线 `core_schema.validate_tool_ids` 的四类错误**一一对应**
（①空 / ②stage 名 / ③一名多写 / ④缺显示名）—— 他们拦，我们自查。
"""
from __future__ import annotations

import csv
import importlib.util
import io
import zipfile
from typing import NamedTuple

import pytest

from conftest import WS_ROOT, seed_core
from batch_fixtures import BATCH as CORE_BATCH
from batch_fixtures import batch_rows, run_rows, sample_rows

from kb.append_pack import build_append_pack
from kb.core_vocab import (TOOL_DISPLAY, TOOL_DISPLAY_INFO, TOOL_ID_SENTINEL,
                           TOOL_UNKNOWN_DISPLAY, resolve_tool)
from kb.expack import (STAGE_CODES, build_expack, export_warnings, extract_rows,
                       parse_expack)

BATCH = "TID-T1"
TOOL_COL, TOOL_ID_COL = 8, 9            # runs.csv 列序（见 expack.build_expack 的表头）

TEM_TMPL = "透射电镜（TEM）"
SEM_TMPL = "扫描电镜（SEM）"


# ================================================================
# 口径表取样：**断言的对象是"当前环境登记了哪些机台"，不是某台真机**
# ================================================================

class _Tool(NamedTuple):
    """一台**已登记**机台：`tid` = core 机台号，`disp` = 口径表里的权威显示名。"""
    tid: str
    disp: str


#: 已登记机台（按口径表顺序；**排除哨兵** —— 哨兵不是"一台机台"，是语义常量）。
#: 本机 = 真清单、公开 clone / CI = 中性样例 ⇒ 两处都够取样（见下面的硬前提）。
REAL: list[_Tool] = [_Tool(tid, disp) for tid, disp in TOOL_DISPLAY.items()
                     if tid != TOOL_ID_SENTINEL]


def tool_at(i: int = 0) -> _Tool:
    """取第 `i` 台已登记机台 —— 跨环境稳定的"真表取样"（本机=真表，CI=中性样例）。"""
    return REAL[i % len(REAL)]


assert len(REAL) >= 3, (
    f"口径表里已登记的机台不足 3 台（{len(REAL)} 台；来源 "
    f"{TOOL_DISPLAY_INFO.get('source')}）⇒ 本文件的取样与判据无法成立")

#: 三条固定锚点：`T0/T1/T2` 是**三台不同**的已登记机台（`tool_at` 取模，≥3 台即互不相同）。
#: 分工：`T0` 配"画布名恰好是 stage 名"的坑、`T1` 配"库内标签 ≠ core 机台号"、
#: `T2` 配"库内 name 恰好等于 core 机台号"。改的是机台号，**坑的语义不变**。
T0: _Tool = tool_at(0)
T1: _Tool = tool_at(1)
T2: _Tool = tool_at(2)

#: 合成画布标签（**故意不进口径表**）—— 扮演"库里写着、core 不认"的画布名。
#: 为什么不用真名：真名在别的机器上未必未登记，判据会随环境变味。
#: ⚠️ 用到它们的用例都会先断言 `not in TOOL_DISPLAY` —— 防判据退化成空转。
CANVAS_RIE = "CANVAS-RIE-X"        # 纯未登记标签（且不是 stage 词）
CANVAS_LITHO = "CANVAS-LITHO-Y"    # 同上，另一台
CANVAS_LABEL = "CANVAS-ETCH-B"     # 库内标签：已登记机台的**画布名**，≠ core 机台号
CANVAS_ICP_D = "CANVAS-ICP-D"      # 库内标签：这台档案没填 core 机台号


def _mod(mid, eq, **kw):
    m = {"id": mid, "equipment_name": eq, "params": {}, "param_outputs": []}
    m.update(kw)
    return m


class _Lib:
    """最小 lib 桩：机台档案**带 `tool_id`**（core 口径）—— 这正是过去导出侧没读的那个字段。

    ⚠️ **机台号与显示名全部取自 `TOOL_DISPLAY`（`T0`/`T1`/`T2`），画布标签是合成的**：
    这样本桩在任何环境里都成立（真表环境验"真机台号能往返"，样例环境验"登记的机台号能往返"），
    而**断言的结构不变** —— 这是"去字面量 ≠ 去检验力"的关键。

    四个机台各占一个坑（改机台号**不改坑**）：
      · `mc_stageish`：画布显示名**恰好是 stage 名** `PECVD`（且 ≠ core 显示名）；
      · `mc_labeled`：库内 label 只是**画布标签**（真值另有 core 机台号）；
      · `mc_selfnamed`：库内 name 恰好**等于** core 机台号（已登记，合法往返）；
      · `mc_notid`：库内档案 `tool_id` 为 `None`（真值未知）⇒ 必须落哨兵，不许编。
    """

    def __init__(self):
        self.data = {
            "machines": [
                {"id": "mc_stageish", "name": "PECVD", "tool_id": T0.tid, "equipment_id": "e1"},
                {"id": "mc_labeled", "name": CANVAS_LABEL, "tool_id": T1.tid, "equipment_id": "e2"},
                {"id": "mc_selfnamed", "name": T2.tid, "tool_id": T2.tid, "equipment_id": "e3"},
                {"id": "mc_notid", "name": CANVAS_ICP_D, "tool_id": None},
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


def _core_schema_or_skip(mod_name: str):
    """只读探针：加载数据线的 `core_schema.py`；**无真源 ⇒ 如实跳过**（不假装通过）。

    ⚠️ 2026-09-28 补第二道跳过：口径表是**加载**来的（`kb/core_vocab`）。若这里只加载到
    中性样例（`TOOL_DISPLAY_INFO["degraded"]`），我们这侧的表**不是权威** ⇒ 拿它去"逐字对拍"
    或"喂给他们的闸"只会制造假绿 ⇒ 如实跳过，理由写清"本机只有中性样例"。
    """
    if TOOL_DISPLAY_INFO.get("degraded"):
        pytest.skip(f"本机只有中性样例（来源 {TOOL_DISPLAY_INFO.get('source')}："
                    f"{TOOL_DISPLAY_INFO.get('path')}）⇒ 口径表不是权威，跨线对拍无意义")
    p = WS_ROOT / "个人空间/18_工艺数据资产/03_实验数据/ingest/core_schema.py"
    if not p.exists():
        pytest.skip("工作区里没有数据线的 core_schema.py（评测环境）")
    spec = importlib.util.spec_from_file_location(mod_name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                                  # 只读导入，不写任何东西
    return mod


# ---------------------------------------------------------------- ② stage 名不是机台号

def test_library_machine_name_is_never_written_as_tool_id():
    """**决定性用例（数据线的 ②）**：画布机台显示名 `PECVD` 恰好是 core 的 **stage 名**，
    导出必须写机台档案里的 core 口径（`T0.tid`）+ 口径表里的唯一显示名。

    仍能抓住：谁把 `machine_name`（画布名）当 `tool_id` 写出去 ⇒ 这里会是 `'PECVD'`
    —— 既撞数据线闸 ②，又拿不到口径表里的显示名 ⇒ 两条断言同时红。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "PECVD", machine_id="mc_stageish", machine_name="PECVD")]}
    row = _runs(proj, _Lib())[0]
    assert row[TOOL_ID_COL] == T0.tid                    # 写的是 core 机台号
    assert row[TOOL_ID_COL] != "PECVD"                   # …而**不是**画布名（那个是 stage 名）
    assert row[TOOL_COL] == T0.disp                      # 显示名以口径表为准
    assert _gate([row]) == []


def test_display_name_never_leaks_into_tool_id_even_without_a_library():
    """没有 lib（评测/命令行环境）：**宁可写哨兵，也不写一个看着像机台号的显示名**。

    实测过的病：画布显示名（库内标签）写进 `tool_id` 会被数据线机台闸**放过**
    （它不是 stage 名、也不重复）⇒ 静默把机台归属记错。这是本文件里最要紧的一条：
    **闸放行 ≠ 口径正确**。

    仍能抓住：没有 lib 时 `tool_id` 取 `machine_name`（或任何非哨兵值）⇒ 红。
    `not in TOOL_DISPLAY` 是**防退化成空转**：哪天一登记这个合成标签，"落哨兵"就变成
    "正确回退到库"，判据必须自己先红，而不是悄悄换了个语义。
    """
    assert CANVAS_RIE not in TOOL_DISPLAY              # 前提：这是**未登记**的库内标签
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "DRIE (Bosch)", machine_name=CANVAS_RIE)]}
    row = _runs(proj)[0]
    assert row[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert row[TOOL_ID_COL] != CANVAS_RIE               # 未登记的画布名不得冒充 core 机台号
    assert row[TOOL_COL] == TOOL_UNKNOWN_DISPLAY
    assert _gate([row]) == []


# ---------------------------------------------------------------- ① 检测节点：不留空

def test_metrology_node_without_a_machine_gets_the_sentinel_not_empty():
    """**数据线的 ①**：TEM 检测节点没有机台 ⇒ 写哨兵，**不许留空**
    （空的语义是"漏填"，与"机台未记录/尚未定"必须分得开）。

    仍能抓住：检测节点被写成空 `tool_id`（或干脆丢节点）⇒ 红。
    """
    proj = {"name": BATCH, "edges": [{"src": "m1", "dst": "m2"}],
            "modules": [_mod("m1", "PECVD"), _mod("m2", TEM_TMPL, subtype="tem")]}
    rows = _runs(proj)
    tem = next(r for r in rows if r[3] == "TEM")
    assert tem[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert tem[TOOL_COL] == TOOL_UNKNOWN_DISPLAY
    assert _gate(rows) == []


def test_library_machine_without_a_tool_id_falls_back_to_the_sentinel():
    """库里机台档案**自己没填 `tool_id`**（真值未知）⇒ 哨兵。**绝不拿显示名顶上。**

    仍能抓住：把库内 label（`CANVAS-ICP-D`）当 core 机台号写出去 ⇒ 红（那是在编归属）。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "ICP Etch", machine_id="mc_notid",
                             machine_name=CANVAS_ICP_D)]}
    row = _runs(proj, _Lib())[0]
    assert row[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert row[TOOL_ID_COL] != CANVAS_ICP_D
    assert _gate([row]) == []


# ---------------------------------------------------------------- 往返（导入 → 导出）

def test_core_tool_identity_round_trips_from_a_package(tmp_path):
    """**往返**：包（无 flow.json，即 core 镜像/手工采集包）→ 画布 → 再导出，机台口径逐字不变。

    修前：导入只留 `machine_name`（库内显示名），再导出就换成显示名 ⇒ 归属漂了。

    CSV 里的 `tool`/`tool_id` 两列**从口径表取**（不再写死真名）：本机验的是真机台号往返，
    CI 验的是样例机台号往返 —— **验的还是同一件事**："core 原值照抄、一个字都不改"。
    ⚠️ 用 `csv.writer` 拼而不是 f-string：显示名可能带逗号（真表里带的是全角括号，但别赌）。
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["run_id", "batch_id", "sample_id", "stage", "stage_seq", "date",
                "tool", "tool_id", "parent_run_id"])
    w.writerow([f"{BATCH}-PECVD-0001", BATCH, "", "PECVD", 1, "2026-09-01",
                T0.disp, T0.tid, ""])
    w.writerow([f"{BATCH}-DRIE-0001", BATCH, "", "DRIE", 2, "2026-09-02",
                T1.disp, T1.tid, f"{BATCH}-PECVD-0001"])
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(f"{BATCH}/runs.csv", buf.getvalue().encode())
        zf.writestr(f"{BATCH}/manifest.json", b'{"batch_id": "TID-T1"}')
    pkg = tmp_path / f"{BATCH}.zip"
    pkg.write_bytes(z.getvalue())
    proj = parse_expack(pkg, None)
    assert [m.get("core_tool_id") for m in proj["modules"]] == [T0.tid, T1.tid]
    rows = _runs(proj)
    got = {r[0]: (r[TOOL_COL], r[TOOL_ID_COL]) for r in rows}
    assert got[f"{BATCH}-PECVD-0001"] == (T0.disp, T0.tid)   # 显示名一字不改
    assert got[f"{BATCH}-DRIE-0001"] == (T1.disp, T1.tid)    # 机台号一字不改
    assert _gate(rows) == []


def test_imported_core_identity_wins_over_a_canvas_side_machine():
    """已入库的 run：机台就是 **core 里那台**，画布上改机台**不改记录**（core 原值优先）。

    理由：`core_run_id` 在 ⇒ 这条 run 在 core 里已有归属，导出只是把记录再带一遍。

    仍能抓住：导出改读画布机台（`mc_stageish` → `T0.tid`）⇒ 就会写成本包另一台机 ⇒ 红。
    这里刻意让画布机台（`T0`）与 core 记录（`T2`）是**两台不同的已登记机台**。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "PECVD", machine_id="mc_stageish", machine_name="PECVD",
                             core_run_id=f"{BATCH}-PECVD-0001",
                             core_tool_id=T2.tid, core_tool=T2.disp)]}
    row = _runs(proj, _Lib())[0]
    assert (row[TOOL_COL], row[TOOL_ID_COL]) == (T2.disp, T2.tid)
    assert row[TOOL_ID_COL] != T0.tid                    # 画布上那台**没有**顶掉 core 记录


# ---------------------------------------------------------------- ③ 一名一写

def test_one_tool_id_has_exactly_one_display_name_in_a_package():
    """**数据线的 ③**：同一台机上的"导入行"与"新节点"必须**同一个显示名**。

    修前：新节点写画布模板名（`RIE` / `Plasma Strip` 这类），导入行写 core 显示名 ⇒ ③ 类错误。

    仍能抓住：同一 `tool_id` 在包内出现两个显示名（新节点写画布模板名、导入行写 core 显示名）
    ⇒ 集合会是两个元素 ⇒ 红。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [
                _mod("m1", "Plasma Strip", machine_id="mc_selfnamed", machine_name=T2.tid),
                _mod("m2", "RIE", core_run_id=f"{BATCH}-RIE-0001",
                     core_tool_id=T2.tid, core_tool=TOOL_DISPLAY[T2.tid]),
            ]}
    rows = _runs(proj, _Lib())
    names = {r[TOOL_COL] for r in rows if r[TOOL_ID_COL] == T2.tid}
    assert names == {T2.disp}
    assert _gate(rows) == []


def test_probe_like_package_no_longer_silently_misattributes_machines():
    """**真工程形状**的包（沉积 / 光刻 / 去胶 / 深硅 / ICP / TEM）过四类闸 + 机台号全对。

    这一条同时是"错机台"的回归锁：修前画布名会被闸**放过**
    （见 `test_display_name_never_leaks_into_tool_id_even_without_a_library`），
    只能靠断言真值守住 —— 所以这里逐台钉住"core 机台号 / 哨兵"。

    仍能抓住：① 库内标签被当成 core 机台号（`DRIE` 那行会写成 `CANVAS-ETCH-B` 而不是 `T1.tid`）；
              ② 不在库里的画布名被顶上去（`LDW` 那行）；③ 档案没填号时编一个（`ICP` 那行）。
    """
    proj = {"name": BATCH, "edges": [{"src": "m5", "dst": "m6"}],
            "modules": [
                _mod("m1", "PECVD", machine_id="mc_stageish", machine_name="PECVD"),
                _mod("m2", "Laser Direct Write", machine_name=CANVAS_LITHO),  # 画布只有名字
                _mod("m3", "Plasma Strip", machine_id="mc_selfnamed", machine_name=T2.tid),
                _mod("m4", "DRIE (Bosch)", machine_id="mc_labeled", machine_name=CANVAS_LABEL),
                _mod("m5", "ICP Etch", machine_id="mc_notid", machine_name=CANVAS_ICP_D),
                _mod("m6", TEM_TMPL, subtype="tem"),
            ]}
    rows = _runs(proj, _Lib())
    assert _gate(rows) == []
    got = {r[3]: r[TOOL_ID_COL] for r in rows}
    assert got["PECVD"] == T0.tid
    assert got["DRIE"] == T1.tid                # core 机台号，不是库内标签 `CANVAS-ETCH-B`
    assert got["ASH"] == T2.tid
    assert got["ICP"] == TOOL_ID_SENTINEL       # 库里没填 tool_id ⇒ 哨兵，不编归属
    assert got["LDW"] == TOOL_ID_SENTINEL       # 这台机不在库桩里 ⇒ 哨兵（不拿画布名顶）
    assert got["LDW"] != CANVAS_LITHO
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
    """追加包与整包**同一处解析**：只改 `expack` 会漏掉这条路（同一个包的两个出口）。

    仍能抓住：追加包这条出口绕过 `resolve_tool`（写库内 label / 留空）⇒ 红。
    """
    proj = {"name": "追加工程", "edges": [],
            "modules": [{"id": "m1", "name": "续做", "equipment_name": "DRIE (Bosch)",
                         "core_run_id": f"{CORE_BATCH}-DRIE-0009", "core_date": "2026-09-20",
                         "machine_id": "mc_labeled", "machine_name": CANVAS_LABEL,
                         "params": {}, "param_outputs": []}]}
    blob, info = build_append_pack(proj, lib=_Lib())
    assert info["ok"], info
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if n.endswith("runs.csv"))
    rows = list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))
    assert rows[0]["tool_id"] == T1.tid
    assert rows[0]["tool"] == T1.disp


# ---------------------------------------------------------------- 【跨线】逐字一致

def test_cross_line_tool_display_is_byte_identical():
    """**跨线逐字判据**：机台表 / 哨兵 / 显示名 —— 我们这侧加载到的口径与数据线 `core_schema.py`
    逐字一致（顺序也算）。

    ⚠️ 只读数据线的文件（`18_工艺数据资产/` 对我们只读）：**无真源则跳过**
    （评测/CI 没有它，或者本机只加载到中性样例）—— 见 `_core_schema_or_skip`。

    仍能抓住：加载器把清单读错/读串（顺序、条目、哨兵）⇒ 与真源对不上 ⇒ 红。
    """
    mod = _core_schema_or_skip("_core_schema_probe_tool")
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

    仍能抓住：`build_expack` 这条出口的口径/列序与 `extract_rows` 漂了（同一个包两个出口）⇒ 红。
    """
    proj = {"name": BATCH, "edges": [{"src": "m4", "dst": "m5"}],
            "modules": [_mod("m1", "PECVD", machine_id="mc_stageish", machine_name="PECVD"),
                        _mod("m4", "DRIE (Bosch)", machine_id="mc_labeled",
                             machine_name=CANVAS_LABEL),
                        _mod("m5", TEM_TMPL, subtype="tem")]}
    data, batch = build_expack(proj, lib=_Lib())
    z = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in z.namelist() if n.endswith("runs.csv"))
    rows = list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))
    assert rows, "包里没有 run 行"
    assert [r["run_id"] for r in rows] == [f"{batch}-PECVD-0001", f"{batch}-DRIE-0001",
                                           f"{batch}-TEM-0001"]
    assert [r["tool_id"] for r in rows] == [T0.tid, T1.tid, TOOL_ID_SENTINEL]
    assert [r["tool"] for r in rows] == [T0.disp, T1.disp, TOOL_UNKNOWN_DISPLAY]


# ---------------------------------------------------------------- 解析器本身

def test_resolve_tool_prefers_id_over_a_stale_display_name():
    """机台档案按 **id** 认（id 是我们自己发的，最硬）；名字只是回退，且**认不到就哨兵**。

    仍能抓住：改成按名字认 ⇒ 会落到哨兵（名字对不上），断言红；且 stage 名当 `core_tool_id`
    时仍必须被兜回到哨兵（② 是格式问题，与登记无关）。
    """
    lib = _Lib()
    tid, name, _ = resolve_tool({"machine_id": "mc_labeled", "machine_name": "写错了的名字"},
                                lib.machines(), STAGE_CODES)
    assert (tid, name) == (T1.tid, T1.disp)
    tid2, name2, _ = resolve_tool({"core_tool_id": "SEM"}, lib.machines(), STAGE_CODES)
    assert (tid2, name2) == (TOOL_ID_SENTINEL, TOOL_UNKNOWN_DISPLAY)   # ② 兜底：stage 名绝不入 tool_id


# ================================================================
# 数据线 2026-09-14 加的第 ⑤ 条闸：`tool_id ∉ TOOL_DISPLAY` ⇒ **拒收整包**
# ================================================================

class _Lib2(_Lib):
    """在 `_Lib` 上补齐**两台"未登记机台"**（形状取自实测的应用库，名字是**合成的**）：

      · `CANVAS-RIE-X`：库内 `tool_id` 就是那串**画布标签**，core 的 `TOOL_DISPLAY` 里**没有**；
      · `MA6`：库内 `tool_id='MA6'`，既没登记，又**与 stage 代号同名** ⇒ 连数据线闸 ② 也会拦。

    为什么用合成标签而不是实测里那两台真名：真名在别的机器上未必未登记，判据会随环境变味。
    要考的性质只有一个 —— "**库内标签 ≠ core 机台号**"。数据线加第 ⑤ 条闸后，"把库内标签当
    core 机台号"会从**静默记错**变成**整包拒收** —— 两种都不能接受 ⇒ 导出必须落哨兵 **并出声**。
    """

    def __init__(self):
        super().__init__()
        self.data["machines"] = self.data["machines"] + [
            {"id": "mc_canvas_rie", "name": CANVAS_RIE, "tool_id": CANVAS_RIE},
            {"id": "mc_ma6", "name": "MA6", "tool_id": "MA6"},
        ]


def test_unregistered_library_tool_id_falls_back_to_the_sentinel():
    """库内机台的 `tool_id` **未登记** ⇒ 落哨兵（不许把应用库的标签冒充 core 机台号）。

    ⚠️ 与"core 原值"分开处理：`core_tool_id` 是**记录**（照抄，哪怕未登记 —— 那是 core 自己的事，
    由闸报出来）；库内 `tool_id` 是我们这边的**标签**，冒充 core 口径就是编。

    仍能抓住：库内未登记标签被直接写进 `tool_id` ⇒ 红（数据线闸 ⑤ 会拒收整包）。
    两条前提断言（`not in TOOL_DISPLAY` / `not in STAGE_CODES`）保证本用例考的确实是**登记**问题，
    而不是被闸 ② 顺手拦下 —— 否则判据会退化成"因为撞了 stage 词才过"。
    """
    assert CANVAS_RIE not in TOOL_DISPLAY               # 前提：未登记
    assert CANVAS_RIE not in STAGE_CODES                # 前提：也不是 stage 词（否则撞的是闸 ②）
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "RIE", machine_id="mc_canvas_rie", machine_name=CANVAS_RIE)]}
    row = _runs(proj, _Lib2())[0]
    assert row[TOOL_ID_COL] == TOOL_ID_SENTINEL
    assert row[TOOL_COL] == TOOL_UNKNOWN_DISPLAY
    assert _gate([row]) == []


def test_machine_named_like_a_stage_is_still_blocked():
    """`MA6` 这种"机台名 == stage 代号"的机器：`tool_id` 绝不能写 `MA6`（闸 ② + ⑤ 双拦）。

    仍能抓住：把库内 `tool_id='MA6'` 当成合法的 core 机台号写出去 ⇒ 红。
    （`MA6` 是 **stage 代号**（契约词表），不是机台身份 —— 所以这个字面量留在这里是对的。）
    """
    assert "MA6" in STAGE_CODES                         # 前提：MA6 是 stage 代号
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "UV Exposure", machine_id="mc_ma6", machine_name="MA6")]}
    row = _runs(proj, _Lib2())[0]
    assert row[3] == "MA6"                       # stage 是 MA6（对）
    assert row[TOOL_ID_COL] == TOOL_ID_SENTINEL  # 机台号不能是 MA6
    assert _gate([row]) == []


def test_unregistered_machine_is_loud_not_silent():
    """**不许静默丢机台名**：落哨兵的同时要把"这台机 core 没登记"讲出来（卡/manifest 里看得见）。

    仍能抓住：`resolve_tool` 落了哨兵却**没有 note**（或告警被吞）⇒ 红。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "RIE", machine_id="mc_canvas_rie", machine_name=CANVAS_RIE)]}
    warns = export_warnings(proj, _Lib2())
    kinds = {w["kind"] for w in warns}
    assert "unregistered_machine" in kinds
    hit = next(w for w in warns if w["kind"] == "unregistered_machine")
    assert CANVAS_RIE in hit["message"] and "UNKNOWN" in hit["message"]


def test_exported_rows_pass_the_data_line_gate():
    """**跨线判据（效力最强的一条）**：把我们的导出结果**直接喂给数据线的** `validate_tool_ids`。

    为什么要这一条：他们的闸是会**长**的 —— 第 ⑤ 条（`tool_id ∉ TOOL_DISPLAY` ⇒ 拒收）就是
    2026-09-14 联调后加的，而"应用库里有未登记机台"这件事**只有这条判据能自动照出来**。
    今后他们再加闸，这条会先红一次，不必等下一轮联调。

    ⚠️ 无真源（`core_schema.py` 不在 / 本机只有中性样例）⇒ 如实跳过，见 `_core_schema_or_skip`。
    """
    mod = _core_schema_or_skip("_core_schema_gate_probe")
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "PECVD", machine_id="mc_stageish", machine_name="PECVD"),
                        _mod("m2", "DRIE (Bosch)", machine_id="mc_labeled",
                             machine_name=CANVAS_LABEL),
                        _mod("m3", "RIE", machine_id="mc_canvas_rie", machine_name=CANVAS_RIE),
                        _mod("m4", "UV Exposure", machine_id="mc_ma6", machine_name="MA6"),
                        _mod("m5", "ICP Etch", machine_id="mc_notid", machine_name=CANVAS_ICP_D),
                        _mod("m6", TEM_TMPL, subtype="tem"),
                        _mod("m7", "Plasma Strip", machine_id="mc_selfnamed",
                             machine_name=T2.tid)]}
    rows = [{"run_id": r[0], "tool": r[TOOL_COL], "tool_id": r[TOOL_ID_COL],
             "stage": r[3]} for r in _runs(proj, _Lib2())]
    assert rows, "一条 run 都没导出来，判据本身失效"
    assert mod.validate_tool_ids(rows) == [], (
        f"我们导出的包会被他们的机台闸拒收：{mod.validate_tool_ids(rows)}")


# ---------------------------------------------------------------- 批次号（幻影批次）

def test_project_name_that_mints_a_phantom_batch_is_reported():
    """**工程名当批次号**：`AR50-T1-明天` 这种工作名 + 一批属于 `AR50-T1` 的 run ⇒
    新节点会被登记成**根本不存在的批次**（数据线 2026-09-14 回执③里正好问到这个）。
    判定："工程里有 run 属于别的批次，而工程名不是那个批次" ⇒ **出声**（不许静默造批次）。

    仍能抓住：`batch_mismatch` 告警被删掉/被吞 ⇒ 红（这里的机台号与本判据无关，只取已登记机台号）。
    """
    proj = {"name": "AR50-T1-明天", "edges": [],
            "modules": [_mod("m1", "PECVD", core_run_id="AR50-T1-PECVD-0001",
                             core_batch_id="AR50-T1", core_tool_id=T0.tid),
                        _mod("m2", "RIE")]}
    warns = export_warnings(proj)
    hit = next((w for w in warns if w["kind"] == "batch_mismatch"), None)
    assert hit is not None, warns
    assert "AR50-T1" in hit["message"] and "AR50-T1-明天" in hit["message"]


def test_no_batch_warning_when_the_project_name_is_the_batch():
    """反例（判据要两向）：工程名**就是**批次号 ⇒ 不出声（否则告警会变成噪音，没人看）。"""
    proj = {"name": "AR50-T1", "edges": [],
            "modules": [_mod("m1", "PECVD", core_run_id="AR50-T1-PECVD-0001",
                             core_batch_id="AR50-T1")]}
    assert [w for w in export_warnings(proj) if w["kind"] == "batch_mismatch"] == []
