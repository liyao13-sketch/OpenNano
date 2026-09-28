"""`07 §G.70` 登记未修的 **expack 字段类**问题 —— 回归锁（2026-09-28 · 工具线）。

对应 `07_工具线_实时状态.md §G.70` 表里的第 1/2/3/4/5/7/8/9 条（第 6 条 `dup_run` 复核后
**已成立**，见文末「已成立」一节 —— 只锁不修）。每条先有红证再修，红证见各用例 docstring。

三条不变量（全部用例共同守的）：
  · **CSV 权威 / 工具不写 core**：一律落 `tmp_path`，core 只读（用 `conftest.seed_core`）；
  · **不推断修补**：认不出的量名/键名**不写**、不发明（宁可空着并出声）；
  · **导出确定性**：同一输入连跑两次，包内 CSV 逐字节相同。

⚠️ 本文件的合成名一律**中性**（`TOOL-A`/`机台甲`…）：真机台名/厂名是公开层指纹，
   写了会在 `test_public_layer_hygiene.py` 上超上限（棘轮只许往下压）。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from conftest import seed_core

BATCH = "E-T1"

#: 包内 runs.csv 的**契约列序**（= 数据线 `core_schema.FIELDS["runs"]`）。
RUN_COLS = ["run_id", "batch_id", "sample_id", "stage", "stage_seq", "date", "t_start",
            "t_end", "tool", "tool_id", "recipe_id", "operator", "purpose",
            "parent_run_id", "env_temp_c", "env_rh_pct", "status", "note",
            "run_nature", "tune_id", "tune_step"]
STEP_COLS = ["step_id", "run_id", "step_order", "machine_step", "step_name", "role",
             "duration_s", "pressure", "pressure_unit", "param_json", "note"]


# ---------------------------------------------------------------- 夹具（一律落 tmp_path）

def _run(rid: str, stage: str, **kw) -> dict:
    r = {"run_id": rid, "batch_id": kw.pop("batch_id", BATCH), "sample_id": "",
         "stage": stage, "stage_seq": kw.pop("stage_seq", "1"), "date": "2026-09-28",
         "t_start": "", "t_end": "", "tool": "", "tool_id": "", "recipe_id": "",
         "operator": "", "purpose": "", "parent_run_id": "", "env_temp_c": "",
         "env_rh_pct": "", "status": "planned", "note": "",
         "run_nature": "", "tune_id": "", "tune_step": ""}
    r.update(kw)
    return r


def _step(rid: str, order: int, name: str, *, dur="", press="", pu="",
          pj=None, role="") -> dict:
    return {"step_id": f"{rid}.S{order:02d}", "run_id": rid, "step_order": order,
            "machine_step": "", "step_name": name, "role": role,
            "duration_s": dur, "pressure": press, "pressure_unit": pu,
            "param_json": json.dumps(pj or {}, ensure_ascii=False), "note": ""}


def _write_csv(p: Path, cols: list[str], rows: list[dict]) -> None:
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def _pkg(tmp_path: Path, batch: str, runs: list[dict], steps: list[dict] | None = None,
         meas: list[dict] | None = None) -> Path:
    """造一个**没有 flow.json** 的包目录 ⇒ 走 `parse_expack` 的 runs/steps 合成分支。"""
    root = Path(tmp_path) / batch
    root.mkdir(parents=True, exist_ok=True)
    _write_csv(root / "runs.csv", RUN_COLS, runs)
    _write_csv(root / "steps.csv", STEP_COLS, steps or [])
    if meas:
        _write_csv(root / "measurements.csv",
                   ["meas_id", "run_id", "sample_id", "quantity", "value", "unit",
                    "method", "loc", "n", "uncertainty", "source_artifact_id",
                    "measured_by", "verification", "note"], meas)
    (root / "manifest.json").write_text(
        json.dumps({"format": "opennano-expack", "batch_id": batch}, ensure_ascii=False),
        encoding="utf-8")
    return root


def _zip_csv(blob: bytes, suffix: str) -> tuple[list[str], list[dict]]:
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if n.endswith(suffix))
    text = z.read(name).decode("utf-8-sig")
    rdr = csv.reader(io.StringIO(text))
    header = next(rdr)
    rows = [dict(zip(header, r)) for r in rdr if r]
    return header, rows


def _zip_member(blob: bytes, suffix: str) -> str:
    z = zipfile.ZipFile(io.BytesIO(blob))
    return z.read(next(n for n in z.namelist() if n.endswith(suffix))).decode("utf-8-sig")


def _zip_manifest(blob: bytes) -> dict:
    return json.loads(_zip_member(blob, "manifest.json"))


class _DefLib:
    """最小 lib 桩：只为**参数键还原**用例提供一张设备模板表。

    为什么需要它：`time_s` / `pass_time_s` / `etch_time_s` 这类**时间列的真名**只有设备模板
    说得清（core 的 `step_name` 是人读步名，不是参数前缀）⇒ 判据必须让模板在场。
    也提供 `build_module` 会调的两个方法，免得走 `LibraryStore`（它会跑迁移链、可能写盘）。
    """

    def __init__(self, name: str, params: dict):
        self.eq = {"id": "eq-tmpl", "name": name, "params": params}
        self.data = {"equipment": {"etch": [self.eq]}, "machines": []}

    def machines(self):
        return []

    def default_equipment_id(self, subtype: str) -> str:
        return self.eq["id"]

    def get_equipment(self, eid: str):
        return self.eq if eid == self.eq["id"] else None


def _p(key, unit="s"):
    return {"label": key, "unit": unit, "default": 1, "min": 0, "max": 1e6}


# ================================================================
# #1 runs.csv 缺 core v0.1.6 的 run_nature / tune_id / tune_step
# ================================================================

def test_runs_csv_带_v016_三列且值不丢():
    """红证（修前）：`build_expack` 的 runs.csv 头只有 18 列（到 `note` 为止），
    而 `parse_expack` 会**读** `run_nature`/`tune_id`/`tune_step` ⇒
    导入 core 再导出 ⇒ season 身份 / 调试线归属**静默消失**（列不存在，读出来恒为空）。
    """
    from kb.expack import build_expack
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "equipment_name": "RIE", "params": {}, "param_outputs": [],
         "run_nature": "season", "tune_id": f"{BATCH}-RIE-TUNE1", "tune_step": 2}]}
    header, rows = _zip_csv(build_expack(proj)[0], "runs.csv")
    assert header == RUN_COLS, f"runs.csv 表头不是 core 契约列序：{header}"
    assert rows[0]["run_nature"] == "season"
    assert rows[0]["tune_id"] == f"{BATCH}-RIE-TUNE1"
    assert rows[0]["tune_step"] == "2"


def test_runs_csv_三列往返_导入拿得回():
    """往返锁：导出的包再被 `parse_expack` 读回，三个键必须原样在模块上。

    红证（修前）：导出侧不写这三列 ⇒ 读回来恒为空（`parse_expack` 那段代码一直是对的，
    病在导出侧）——「写得出、读得回」才算往返成立。
    """
    from kb.expack import build_expack, parse_expack
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "equipment_name": "RIE", "params": {}, "param_outputs": [],
         "run_nature": "trial", "tune_id": f"{BATCH}-RIE-TUNE1", "tune_step": 3}]}
    blob, batch = build_expack(proj)
    z = zipfile.ZipFile(io.BytesIO(blob))
    # 有 flow.json 会走"保布局"分支（不读 runs 列）⇒ 去掉它，逼走 runs/steps 合成分支
    files = {n: z.read(n) for n in z.namelist() if not n.endswith("flow.json")}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
        for n, d in files.items():
            w.writestr(n, d)
    back = parse_expack(_write_zip(out.getvalue()), None)
    m = back["modules"][0]
    assert m.get("run_nature") == "trial"
    assert m.get("tune_id") == f"{BATCH}-RIE-TUNE1"
    assert m.get("tune_step") == 3


def _write_zip(blob: bytes) -> Path:
    import tempfile
    p = Path(tempfile.mkdtemp(prefix="expack_fields_")) / "pkg.zip"
    p.write_bytes(blob)
    return p


def test_append_pack_runs_csv_同病同修(tmp_path, monkeypatch):
    """红证（修前）：追加包 runs.csv 是**另一处手写表头**，与整包同样少这三列 ⇒ 新 run 的
    season/调试线归属落库即消失（`07 §G.70` 明写"append_pack 同病"）。"""
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", runs=[_run(f"{BATCH}-PECVD-0001", "PECVD")])
    monkeypatch.setattr(ap, "CORE_DIR", d, raising=False)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "equipment_name": "RIE", "params": {}, "param_outputs": [],
         "core_run_id": f"{BATCH}-RIE-0002", "core_batch_id": BATCH, "core_stage": "RIE",
         "core_stage_seq": "2", "run_nature": "season",
         "tune_id": f"{BATCH}-RIE-TUNE1", "tune_step": 1}]}
    blob, info = ap.build_append_pack(proj)
    assert info["ok"], info
    header, rows = _zip_csv(blob, "runs.csv")
    assert header == RUN_COLS, header
    assert rows[0]["run_nature"] == "season"
    assert rows[0]["tune_id"] == f"{BATCH}-RIE-TUNE1"
    assert rows[0]["tune_step"] == "1"


# ================================================================
# #2 画布参数键往返被加前缀污染
# ================================================================

def test_导入不给参数键加组名前缀(tmp_path):
    """红证（修前）：`_module_from_run` 拿 `step_name` 当参数前缀往回拼
    （`params[f"{pre}_{k}"]`）⇒ 组名 `main` 被拼进**每一个**键：
    `time → main_time`、`gas_SF6 → main_gas_SF6`、`pressure → main_pressure`
    ⇒ 面板按设备模板键名找参数，一个都对不上 ⇒ **全显默认值**。

    正解：`param_json` 里的键**本来就是契约键名**（导出侧从不剥前缀）⇒ 原样透传。
    """
    from kb.expack import parse_expack
    rid = f"{BATCH}-RIE-0001"
    root = _pkg(tmp_path, BATCH, [_run(rid, "RIE", tool_id="UNKNOWN")], [
        _step(rid, 1, "main", press="50", pu="mTorr",
              pj={"time": 120, "gas_SF6": 30})])
    m = parse_expack(root, None)["modules"][0]
    assert set(m["params"]) == {"time", "gas_SF6", "pressure"}, m["params"]
    assert m["params"]["time"] == 120


def test_导入按设备模板还原独立列的键名(tmp_path):
    """红证（修前）：`duration_s`/`pressure` 是 steps.csv 的**独立列**，
    导入侧用 `f"{pre}_duration_s"` 拼回 ⇒ `etch_time_s → etch_duration_s`
    （面板上 `etch_time_s` 找不到值）。正解：**按设备模板认键名**
    （模板里以该组名开头、且是时间/压强语义的键**唯一**时才认，认不出就留列名，不发明）。
    """
    from kb.expack import parse_expack
    rid = f"{BATCH}-DRIE-0001"
    lib = _DefLib("DRIE (Bosch)", {
        "etch_gas_SF6": _p("SF6", "sccm"), "etch_source_power": _p("SP", "W"),
        "etch_bias_power": _p("BP", "W"), "etch_pressure": _p("ETCH-P", "mTorr"),
        "etch_time_s": _p("ETCH-T"),
    })
    root = _pkg(tmp_path, BATCH, [_run(rid, "DRIE", tool_id="UNKNOWN")], [
        _step(rid, 1, "etch", dur="7", press="30", pu="mTorr",
              pj={"etch_source_power": 1200})])
    m = parse_expack(root, lib)["modules"][0]
    assert set(m["params"]) == {"etch_source_power", "etch_time_s", "etch_pressure"}, m["params"]
    # 值本身照 CSV 原样（独立列读出来是字符串，与修前一致；这里只钉**键名**）
    assert float(m["params"]["etch_time_s"]) == 7


# ================================================================
# #3 未映射的中文接口名被当 measurements.quantity 写 core
# ================================================================

def test_未映射的中文接口名不写进_measurements():
    """红证（修前）：`param_outputs` 是设备模板的**画布中文接口名**（实测 17 个里 9 个在
    `PARAM_TO_QUANTITY` 里没有量名，如 `套刻精度`/`形貌缺陷`/`表面脏污`）；
    原实现在映射不中时 `q = out` ⇒ 中文名**原样**落 `measurements.quantity`
    ⇒ 非受控量名，任何视图都取不到。正解：**未映射 ⇒ 不写这一行**。
    """
    from kb.expack import extract_rows
    mods = [{"id": "m1", "equipment_name": "RIE", "params": {},
             "param_outputs": ["套刻精度", "硅CD", "表面脏污"]}]
    _, _, meas, _, _ = extract_rows({"name": BATCH, "edges": [], "modules": mods}, lib=None)
    got = {r[3] for r in meas}
    assert got == {"final_cd_nm"}, got


def test_英文受控量名照旧透传():
    """反向锁（防"修过头"）：core 真数据里的量名有一批**不在**本模块映射表里
    （`resist_thickness_nm`/`pitch_nm`/`loop_count`…）却确实是受控记录
    ⇒ 判据只能拦**中文接口名**，不能因为"我没见过这个英文名"就丢掉。"""
    from kb.expack import extract_rows
    mods = [{"id": "m1", "equipment_name": "RIE", "params": {},
             "param_outputs": ["resist_thickness_nm"]}]
    _, _, meas, _, _ = extract_rows({"name": BATCH, "edges": [], "modules": mods}, lib=None)
    assert [r[3] for r in meas] == ["resist_thickness_nm"]


def test_未映射的接口名要出声():
    """未映射**不静默**：必须进导出告警（manifest.warnings / 流程卡都会列出）。"""
    from kb.expack import export_warnings
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "equipment_name": "RIE", "params": {},
         "param_outputs": ["套刻精度"]}]}
    hit = [w for w in export_warnings(proj) if w["kind"] == "unmapped_quantity"]
    assert hit and "套刻精度" in hit[0]["message"], export_warnings(proj)


# ================================================================
# #4 导入后节点名写成机台显示名 / 哨兵串
# ================================================================

def test_节点名来自设备模板名_不是机台显示名(tmp_path):
    """红证（修前）：`_module_from_run` 用 `name=r.get("tool")` ——
    **`runs.tool` 是机台显示名**（core 口径），于是同一台机上的 6 条 ICP 全同名、
    机台未记录时节点名直接变成哨兵串（`UNKNOWN（机台未记录）`）—— 画布读不出这是什么工序。

    正解：节点名取**工艺/设备模板名**（`STAGE_TO_TEMPLATE` 的模板名）；
    机台归属另有 `machine_name`/`core_tool_id` 表达，不必也不该占节点名。
    """
    from kb.expack import STAGE_TO_TEMPLATE, parse_expack
    disp = "TOOL-DISP-XYZ（画布上不该出现的机台显示名）"
    rid = f"{BATCH}-RIE-0001"
    root = _pkg(tmp_path, BATCH, [_run(rid, "RIE", tool=disp, tool_id="UNKNOWN")])
    m = parse_expack(root, None)["modules"][0]
    assert disp not in m["name"], m["name"]
    assert m["name"] == STAGE_TO_TEMPLATE["RIE"][1], m["name"]


def test_哨兵机台的节点名不是哨兵串(tmp_path):
    """机台未记录（哨兵）时节点名同样不许退化成一串哨兵。"""
    from kb.expack import parse_expack
    from kb.core_vocab import TOOL_ID_SENTINEL
    rid = f"{BATCH}-ICP-0001"
    root = _pkg(tmp_path, BATCH, [_run(rid, "ICP", tool=TOOL_ID_SENTINEL,
                                      tool_id=TOOL_ID_SENTINEL)])
    name = parse_expack(root, None)["modules"][0]["name"]
    assert name and TOOL_ID_SENTINEL not in name, name


# ================================================================
# #5 resolve_stage 不看 core_stage
# ================================================================

def test_resolve_stage_优先_core_stage():
    """红证（修前）：`resolve_stage` 只按 `equipment_name`/`subtype` 推 ⇒ 回灌节点若设备名不在
    映射表，stage 被**静默改写成大类兜底值**（实测三条不同工序的模块全被解析成同一个 stage）。
    正解：`core_stage` 优先（与 `core_tool_id`/`core_run_id` 同一套「core 原值优先」口径）。
    """
    from kb.expack import resolve_stage
    m = {"core_stage": "SEM", "equipment_name": "设备名不在映射表里-X", "subtype": "etch"}
    assert resolve_stage(m) == "SEM", resolve_stage(m)


def test_resolve_stage_非法_core_stage_不硬塞():
    """`core_stage` 不是词表代号时不硬塞（否则会把脏值一路写进 runs.csv）；
    退回原三级回退 —— 与"永不发明"一致。"""
    from kb.expack import resolve_stage
    m = {"core_stage": "NOT-A-STAGE", "equipment_name": "RIE", "subtype": "etch"}
    assert resolve_stage(m) == "RIE"


def test_core_stage_来源要出声():
    """`core_stage` 优先**要留告警说明来源**（否则"改写过"与"本来如此"无法区分）。"""
    from kb.expack import export_warnings
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "name": "甲", "core_run_id": f"{BATCH}-SEM-0001", "core_stage": "SEM",
         "equipment_name": "设备名不在映射表里-X", "subtype": "etch",
         "params": {}, "param_outputs": []}]}
    hit = [w for w in export_warnings(proj) if w["kind"] == "stage_from_core"]
    assert hit and "SEM" in hit[0]["message"], export_warnings(proj)


def test_导入把_core_stage_带回来(tmp_path):
    """往返前提：`parse_expack` 必须像 `core_tool_id`/`core_stage_seq` 一样把 core 的
    `stage` 原值带上（否则"core_stage 优先"在真包上永远没有输入）。"""
    from kb.expack import parse_expack
    rid = f"{BATCH}-PECVD-0001"
    root = _pkg(tmp_path, BATCH, [_run(rid, "PECVD")])
    assert parse_expack(root, None)["modules"][0].get("core_stage") == "PECVD"


# ================================================================
# #6 复制节点同 core_run_id —— 复核：**已成立**（只锁不修）
# ================================================================

def test_复制节点同_run_id_有_dup_run_告警():
    """`07 §G.70` 第 6 条（P2）登记"复制节点同 `core_run_id` 不去重、无告警"。

    **复核结论：不成立（已修）** —— `kb/layout_audit.audit()` ② 段一直有 `dup_run` 检查，
    本用例把它钉住（防以后被"顺手删掉"）。⇒ 按清单要求**如实记为已修并跳过**，不补代码。
    """
    from kb.layout_audit import audit
    dup = {"id": "m1", "name": "甲", "core_run_id": f"{BATCH}-RIE-0001",
           "core_stage": "RIE", "x": 0, "y": 0}
    proj = {"name": BATCH, "edges": [], "modules": [dup, {**dup, "id": "m2", "x": 400}]}
    kinds = {i["kind"] for i in audit(proj)["issues"]}
    assert "dup_run" in kinds, audit(proj)["issues"]


# ================================================================
# #7 eq_state.state_id 同日同机台撞号
# ================================================================

def _eq_proj(rows: list[dict]) -> dict:
    return {"name": BATCH, "edges": [], "modules": [], "core_eq_state": rows}


def test_eq_state_同日同机台不撞号():
    """红证（修前）：`sid = f"EQ-{date}-{tool}"` ⇒ 同一天同一台机的**第二条起撞号**。
    撞号的后果不是"难看"，是**丢数据**：数据线 `datasets_folder.py` 见 `state_id` 已存在就
    `continue`（静默跳过），而 `core_schema` 的 QA 也把 `state_id 唯一` 当硬项。
    """
    from kb.expack import build_expack
    blob, _ = build_expack(_eq_proj([
        {"date": "2026-09-28", "tool": "TOOL-A", "env_temp_c": "22", "note": "上午"},
        {"date": "2026-09-28", "tool": "TOOL-A", "env_temp_c": "23", "note": "下午"},
        {"date": "2026-09-28", "tool": "TOOL-B", "env_temp_c": "21"},
    ]))
    _, rows = _zip_csv(blob, "eq_state.csv")
    sids = [r["state_id"] for r in rows]
    assert len(rows) == 3 and len(set(sids)) == 3, sids
    # 逐行对得上（不许把值串行）
    got = {r["note"]: r["env_temp_c"] for r in rows if r["note"]}
    assert got == {"上午": "22.0", "下午": "23.0"}, got


def test_导出确定性_同一输入两次逐字节相同():
    """硬性要求：同一输入连跑两次，包内 **所有 CSV** 逐字节相同（含 eq_state 的去重序号）。"""
    from kb.expack import build_expack
    proj = _eq_proj([
        {"date": "2026-09-28", "tool": "TOOL-A", "env_temp_c": "22"},
        {"date": "2026-09-28", "tool": "TOOL-A", "env_temp_c": "23"},
        {"date": "2026-09-28", "tool": "TOOL-B", "env_temp_c": "21"},
    ])
    proj["modules"] = [{"id": "m1", "equipment_name": "RIE", "params": {},
                        "param_outputs": ["硅CD"], "run_nature": "trial",
                        "tune_id": f"{BATCH}-RIE-TUNE1", "tune_step": 1}]

    def csvs(blob):
        z = zipfile.ZipFile(io.BytesIO(blob))
        return {n.rsplit("/", 1)[-1]: z.read(n)
                for n in z.namelist() if n.endswith(".csv")}

    a, b = csvs(build_expack(proj)[0]), csvs(build_expack(proj)[0])
    assert set(a) == set(b) and a, sorted(a)
    for k in a:
        assert a[k] == b[k], f"{k} 两次导出不一致"


# ================================================================
# #8 流程卡「（无参数）」行少一格
# ================================================================

def test_流程卡_无参数行_列数与表头一致():
    """红证（修前）：无参数分支写的是 `| S01 | （无参数） | |` —— 比表头少一格
    （markdown 表格列数不齐，渲染出来会串列/缺列）。"""
    from kb.expack import build_process_card
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "equipment_name": "RIE", "params": {}, "param_outputs": [],
         "core_menu_steps": [{"step_order": 1, "step_name": "空步", "role": "",
                              "duration_s": 0, "param_json": {}}]}]}
    lines = build_process_card(proj).splitlines()
    hdr = next(ln for ln in lines if ln.startswith("| 步 |"))
    row = next(ln for ln in lines if "（无参数）" in ln)
    assert row.count("|") == hdr.count("|"), (hdr, row)


# ================================================================
# #9 词表外 obs 静默丢弃无计数
# ================================================================

def test_词表外现象要计数进_manifest(tmp_path, monkeypatch):
    """红证（修前）：`_form_observations` 对表外 `obs_type` 直接 `continue`，**不计数、不出声**
    ⇒ 用户以为现象进去了，其实一个字都没落。正解：照 `append_pack` 的口径计数并写进 manifest。
    """
    from kb import form_contract as fc
    from kb.expack import build_expack
    monkeypatch.setattr(fc, "observations",
                        lambda: [{"obs_type": "划痕"}, {"obs_type": "颗粒"}])
    proj = {"name": BATCH, "edges": [], "modules": [
        {"id": "m1", "equipment_name": "RIE", "params": {}, "param_outputs": [],
         "core_observations": [
             {"obs_type": "划痕", "severity": "轻", "description": "边缘一道"},
             {"obs_type": "外星现象", "description": "不在词表里"},
             {"obs_type": "", "description": "空类型"}]}]}
    blob, _ = build_expack(proj)
    mf = _zip_manifest(blob)
    assert mf.get("observations_skipped") == ["外星现象"], mf
    _, rows = _zip_csv(blob, "observations.csv")
    assert [r["obs_type"] for r in rows] == ["划痕"]
