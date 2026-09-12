"""追加包（`kb/append_pack.py`）—— 回归网。

钉住的是**数据线验收过的四条口径**（2026-09-13 三轮验收）：
  · 只带 core 里**还没有**的 run（既有源优先 ⇒ 老行永不被覆盖）
  · `source=tool-append`（**不是 core-slice** ⇒ 不会被 `discover()` 整包跳过）
  · `sample_id` / `date` / `stage_seq` 每行都要报**来源**（含"值正确"的情况）
  · 空值语义：`value` 空 = 未测（**不补 0**）；表外 `obs_type` 跳过并计数
"""
from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from conftest import seed_core
from batch_fixtures import BATCH, ROOT, batch_rows, run_rows, sample_rows

NEW_RUN = f"{BATCH}-RIE-0001"


@pytest.fixture
def core(tmp_path, monkeypatch):
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", runs=run_rows(), batches=batch_rows(),
                  samples=sample_rows(),
                  steps=[{"step_id": f"{BATCH}-LDW-0001.S01", "run_id": f"{BATCH}-LDW-0001",
                          "step_order": "1", "machine_step": "1", "step_name": "etch-01",
                          "role": "etch", "duration_s": "10",
                          "param_json": '{"gvv1": 0, "gvv2": 0, "apc1_press": 2}'}],
                  measurements=[{"meas_id": "M-1", "run_id": f"{BATCH}-LDW-0001",
                                 "quantity": "线宽", "value": "", "unit": "nm"},
                                {"meas_id": "M-2", "run_id": f"{BATCH}-LDW-0001",
                                 "quantity": "线宽", "value": "512", "unit": "nm"}])
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    return d


def _project(sample="", date="2026-09-20", with_steps=True, obs=None, meas=None):
    """一个"画布工程"：1 条 core 里没有的新 run。"""
    m = {"id": "m-new", "name": "RIE 续做", "core_run_id": NEW_RUN,
         "core_parent_run_id": f"{BATCH}-DRIE-0002", "run_state": "planned",
         "machine_name": "RIE-400iPB", "equipment_name": "RIE"}
    if sample:
        m["core_sample_id"] = sample
    if date:
        m["core_date"] = date
    if with_steps:
        m["core_menu_steps"] = [
            {"step_order": 1, "step_name": "chuck-01", "role": "chuck", "duration_s": 5,
             "param_json": {"machine_step": 1, "gvv1": 0, "gvv2": 0}},
            {"step_order": 2, "step_name": "etch-03", "role": "etch", "duration_s": 30,
             "param_json": {"machine_step": 3, "gvv1": 0}},
        ]
    if meas is not None:
        m["core_measurements"] = meas
    if obs is not None:
        m["core_observations"] = obs
    return {"name": "回归工程", "modules": [m]}


def _unzip(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    return z, {n: z.read(n) for n in z.namelist()}


def _rows(text: bytes):
    return list(csv.DictReader(io.StringIO(text.decode("utf-8-sig"))))


# ------------------------------------------------------------------ 只带新 run
def test_只带_core_里没有的_run(core):
    from kb.append_pack import build_append_pack, new_runs_of
    proj = _project()
    assert new_runs_of(proj) == [NEW_RUN]
    blob, summary = build_append_pack(proj, purpose="DRIE 后续", operator="owner")
    assert summary["ok"] and summary["new_runs"] == [NEW_RUN]
    z, files = _unzip(blob)
    runs = _rows(files[f"{BATCH}_append/runs.csv"])
    assert [r["run_id"] for r in runs] == [NEW_RUN]
    # 老 run **一行都不能进**（既有源优先）
    assert all(r["run_id"] not in {x["run_id"] for x in run_rows()} for r in runs)


def test_没有新_run_就不出包(core):
    from kb.append_pack import build_append_pack
    proj = {"name": "全都在 core 里", "modules": [{"core_run_id": f"{BATCH}-LDW-0001",
                                                   "core_date": "2026-09-02"}]}
    blob, summary = build_append_pack(proj)
    assert blob is None and summary["ok"] is False
    assert "无需导出追加包" in summary["reason"]


# ------------------------------------------------------------------ manifest
def test_manifest_是_tool_append_且逐行报来源(core):
    from kb.append_pack import build_append_pack
    blob, _ = build_append_pack(_project(sample=f"{BATCH}-01-DIE4"), operator="owner")
    _, files = _unzip(blob)
    man = json.loads(files[f"{BATCH}_append/manifest.json"])
    assert man["source"] == "tool-append"          # ★ 不是 core-slice ⇒ discover() 不会跳
    assert man["format"] == "opennano-expack" and man["pack_type"] == "append"
    assert man["only_new_rows"] is True
    assert man["new_runs"] == [NEW_RUN] and man["runs"] == 1
    # 三个来源表都要有这一行（值正确也要报来源）
    assert man["sample_id_source"][NEW_RUN] == "模块自带（画布/导入包）"
    assert man["date_source"][NEW_RUN] == "画布计划日期"
    assert "**" not in man["date_source"][NEW_RUN]          # 没有"退回今天"的告警
    assert man["stage_seq_source"][NEW_RUN]                  # 取自 core 同 stage
    assert "追加包" in man["note"]
    readme = files[f"{BATCH}_append/包说明.md"].decode()
    assert "入库口径" in readme and "既有源优先" in readme      # 只并入、不改老行
    assert "不要补 0" in readme


def test_缺计划日期要退回今天并显式告警(core):
    from kb.append_pack import build_append_pack
    blob, _ = build_append_pack(_project(date=""))
    _, files = _unzip(blob)
    man = json.loads(files[f"{BATCH}_append/manifest.json"])
    assert "退回导出当天" in man["date_source"][NEW_RUN]
    runs = _rows(files[f"{BATCH}_append/runs.csv"])
    assert runs[0]["date"]                                # 有值（就是今天），但来源已标红


def test_sample_id_缺失时继承_core_真实归属(core):
    """**绝不凭空造 die 号**：模块没标 ⇒ 从 core 该 run / 其上游继承，并写明继承自谁。"""
    from kb.append_pack import build_append_pack
    blob, _ = build_append_pack(_project(sample=""))
    _, files = _unzip(blob)
    runs = _rows(files[f"{BATCH}_append/runs.csv"])
    # 上游 DRIE-0002 的 sample 是 DIE15-01 ⇒ 继承它，而不是留空、更不是猜
    assert runs[0]["sample_id"] == f"{BATCH}-01-DIE15-01"
    man = json.loads(files[f"{BATCH}_append/manifest.json"])
    assert man["sample_id_source"][NEW_RUN].startswith("继承自 core:")


def test_sample_id_两处都没有要写明空缺(core):
    """上游也没有 ⇒ 明写空缺（**必须让人看见**，不许静默）。"""
    from kb.append_pack import build_append_pack
    proj = _project(sample="")
    proj["modules"][0]["core_parent_run_id"] = f"{BATCH}-ICP-0004"    # core 里 sample 为空
    blob, _ = build_append_pack(proj)
    _, files = _unzip(blob)
    man = json.loads(files[f"{BATCH}_append/manifest.json"])
    assert "空缺" in man["sample_id_source"][NEW_RUN]


# ------------------------------------------------------------------ steps / 空值语义
def test_steps_保留机台槽位号且不补零(core):
    from kb.append_pack import build_append_pack
    blob, summary = build_append_pack(_project())
    _, files = _unzip(blob)
    steps = _rows(files[f"{BATCH}_append/steps.csv"])
    assert summary["steps"] == 2
    assert [s["step_id"] for s in steps] == [f"{NEW_RUN}.S01", f"{NEW_RUN}.S02"]
    assert [s["machine_step"] for s in steps] == ["1", "3"]      # 机台槽位号不丢
    pj = json.loads(steps[0]["param_json"])
    assert pj["gvv1"] == 0 and pj["gvv2"] == 0                    # 0 值也存键
    assert "machine_step" not in pj and "phase" not in pj         # 结构键升列，不塞回参数
    assert steps[0]["pressure"] == ""                             # 没有的键**不补 0**


def test_measurements_空值不写_不补零(core):
    from kb.append_pack import build_append_pack
    proj = _project(meas=[
        {"meas_id": "M-a", "sample_id": ROOT, "quantity": "线宽", "value": "", "unit": "nm"},
        {"meas_id": "M-b", "sample_id": ROOT, "quantity": "线宽", "value": "480", "unit": "nm",
         "verification": "已核实"},
    ])
    blob, summary = build_append_pack(proj)
    _, files = _unzip(blob)
    meas = _rows(files[f"{BATCH}_append/measurements.csv"])
    assert [m["meas_id"] for m in meas] == ["M-b"]                # 空值那行**不进包**
    assert summary["measurements"] == 1
    assert meas[0]["value"] == "480" and meas[0]["verification"] == "已核实"


def test_observations_表外词跳过并计数(core, monkeypatch):
    """受控词表是**权威**：表外 obs_type 跳过（不静默塞进包）。"""
    from kb import append_pack as ap
    monkeypatch.setattr(ap.fc, "observations", lambda: [{"obs_type": "颗粒"}])
    blob, summary = ap.build_append_pack(_project(obs=[
        {"obs_type": "颗粒", "severity": "轻", "description": "边缘有颗粒"},
        {"obs_type": "自造词", "severity": "轻", "description": "不该进包"},
    ]))
    _, files = _unzip(blob)
    obs = _rows(files[f"{BATCH}_append/observations.csv"])
    assert [o["obs_type"] for o in obs] == ["颗粒"]
    assert summary["skipped_obs"] == ["自造词"]


# ------------------------------------------------------------------ load_run_chain
def test_load_run_chain_只拉非空测量且_id_照抄(core):
    from kb.append_pack import load_run_chain
    ch = load_run_chain(BATCH)
    assert ch["nodes"] == len(run_rows())
    assert ch["edges"] == sum(1 for r in run_rows() if r.get("parent_run_id"))
    by_id = {r["run_id"]: r for r in ch["chain"]}
    ldw = by_id[f"{BATCH}-LDW-0001"]
    assert [m["meas_id"] for m in ldw["measurements"]] == ["M-2"]   # 空值不进
    assert ch["skipped"]["measurements_blank"] == 1
    assert ldw["steps"][0]["param_json"]["gvv1"] == 0               # param_json 原样
    # 排序按 stage_seq
    seqs = [int(r["stage_seq"]) for r in ch["chain"]]
    assert seqs == sorted(seqs)
    assert "env_* 空就留空" in ch["note"]                           # 不拿 eq_state 顶替


# ------------------------------------------------------------------ core → 画布
def test_core_to_project_回灌形状一致(core):
    from kb.append_pack import core_to_project
    proj = core_to_project(BATCH, project_name="回灌测试")
    ids = [m.get("core_run_id") for m in proj["modules"]]
    assert sorted(ids) == sorted(r["run_id"] for r in run_rows())
    assert proj["core_batch_id"] == BATCH
    assert proj["_core_to_canvas"]["source"] == "core(只读)"
    mod = {m["core_run_id"]: m for m in proj["modules"]}[f"{BATCH}-LDW-0001"]
    # 空测量不进画布（不渲染成 0）
    vals = [x for x in (mod.get("core_measurements") or [])]
    assert all(str(v.get("value")).strip() != "" for v in vals)


def test_core_to_project_没有该批次要报错(core):
    from kb.append_pack import core_to_project
    with pytest.raises(ValueError):
        core_to_project("NO-SUCH-BATCH")
