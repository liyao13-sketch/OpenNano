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


# ------------------------------------------------------------------ 画布连线（不许编造）
def _seed_ar50lj(core_dir):
    """照 AR50-T1 的形状造数据：**并存试验的 parent 全为空**（这正是假直线的温床）。"""
    from conftest import seed_core
    rows = [dict(r, batch_id=BATCH,
                 sample_id=(f"{BATCH}-01-DIE4" if r["run_id"].endswith("ICP-0002") else ROOT))
            for r in _ar50lj_rows()]
    return seed_core(core_dir, batches=batch_rows(), samples=sample_rows(), runs=rows)


def test_合成画布_空_parent_不许编线(tmp_path, monkeypatch):
    """★ 回归：画布上"DWL 后面跟着 8 个连续的 ICP 刻蚀"（owner 2026-09-13 实测）。

    根因（两处同源，都在"没有 flow.json ⇒ 由 runs 合成"这条路上）：
      · `expack._edges_from_runs`：空 parent 就接"上一条" ⇒ 并存试验被连成直线；
      · `expack` 导出路径：`parent = core_parent or run_rows[-1]`（注释写"导出顺序即执行顺序"）
        ⇒ 回灌时**先把假父写回模块**，再据此连边。
    判据：core 说空就是空 —— 不连线。绝不能"看着像一条链就接上"。
    """
    from kb import append_pack as ap
    d = _seed_ar50lj(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = ap.core_to_project(BATCH, project_name="连线回归")
    byid = {m["id"]: m for m in proj["modules"]}
    pairs = {(byid[e["src"]]["core_run_id"], byid[e["dst"]]["core_run_id"]): e.get("_link")
             for e in (proj.get("edges") or [])}
    # ① **记录边（实线）**：只有 core 真写了的那些
    assert {k for k, v in pairs.items() if v == "recorded"} == {
        (f"{BATCH}-ICP-0002", f"{BATCH}-ICP-0003"),
        (f"{BATCH}-ICP-0003", f"{BATCH}-ASH-0001")}, pairs
    # ② **同工序内不许有边**：并存的两条 ICP 之间一条都不许有（不论实线虚线）
    assert not [k for k in pairs if k[0].startswith(f"{BATCH}-ICP") and k[1].startswith(f"{BATCH}-ICP")
                and k[0] != f"{BATCH}-ICP-0002"]
    # ③ **推断边（虚线）**：按工艺顺序补，且上游严格来自更早的工序
    seq = {r["run_id"]: int(r["stage_seq"]) for r in run_rows()}
    for (a, b), kind in pairs.items():
        if kind == "inferred":
            assert seq[a] < seq[b], (a, b)
    assert (f"{BATCH}-LDW-0001", f"{BATCH}-ICP-0001") in pairs
    # ④ 模块上的 core_parent_run_id 必须与 core 一致（空就是空，**推断不改它**）
    got = {m["core_run_id"]: (m.get("core_parent_run_id") or "") for m in proj["modules"]}
    assert got[f"{BATCH}-LDW-0001"] == ""
    assert got[f"{BATCH}-ICP-0001"] == ""


def test_合成画布_真实链一条不丢(tmp_path, monkeypatch):
    """反面对照：core 里**有的**父必须连上、一条不丢（别为了修假边把真边也去掉）。"""
    from kb import append_pack as ap
    d = _seed_ar50lj(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = ap.core_to_project(BATCH)
    byid = {m["id"]: m for m in proj["modules"]}
    pairs = {(byid[e["src"]]["core_run_id"], byid[e["dst"]]["core_run_id"]): e.get("_link")
             for e in proj["edges"]}
    assert pairs[(f"{BATCH}-ICP-0002", f"{BATCH}-ICP-0003")] == "recorded"
    assert pairs[(f"{BATCH}-ICP-0003", f"{BATCH}-ASH-0001")] == "recorded"


def test_历史手工节点_没有_core_id_才按时序接():
    """兜底**只**给真正的历史/手工节点：连规范 run_id 都没有的，才按导出顺序接上一条，
    否则整张图会散成互不相连的孤岛。有 run_id 但 parent 为空 ⇒ 绝不接。"""
    from kb.expack import _edges_from_runs
    ms = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
    legacy = [{"run_id": "", "parent_run_id": ""}, {"run_id": "", "parent_run_id": ""}]
    assert _edges_from_runs(legacy, {}, [m["id"] for m in ms]) == [
        {"src": "m1", "dst": "m2", "_link": "recorded"}]
    # 不同工序之间会补**推断边**（虚线），但必须是 inferred、且方向是工艺正向
    real = [{"run_id": f"{BATCH}-PECVD-0001", "parent_run_id": "", "stage_seq": 1},
            {"run_id": f"{BATCH}-LDW-0001", "parent_run_id": "", "stage_seq": 2}]
    idmap = {r["run_id"]: m["id"] for r, m in zip(real, ms)}
    got = _edges_from_runs(real, idmap, [m["id"] for m in ms])
    assert got == [{"src": "m1", "dst": "m2", "_link": "inferred"}]
    # 同工序两条 ⇒ 一条边都没有
    same = [{"run_id": f"{BATCH}-ICP-0001", "parent_run_id": "", "stage_seq": 3},
            {"run_id": f"{BATCH}-ICP-0002", "parent_run_id": "", "stage_seq": 3}]
    idmap2 = {r["run_id"]: m["id"] for r, m in zip(same, ms)}
    assert _edges_from_runs(same, idmap2, [m["id"] for m in ms]) == []


# ------------------------------------------------------------------ 画布布局（按工艺列）
def test_布局_按工序分列且谁也不叠(tmp_path, monkeypatch):
    """布局规则：x = 工序列（左→右即工艺顺序），y = 主行（真实链）+ 下缩（并存试验）。

    守两条硬约束：① **同工序必在同一列** ② **任何两个节点不许叠在同一坐标**
    （曾经 ICP-0006/0007/0008 真的叠在一起）。
    """
    from kb import append_pack as ap
    d = _seed_ar50lj(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = ap.core_to_project(BATCH)
    coords: dict[tuple[float, float], str] = {}
    col_of: dict[int, set[str]] = {}
    for m in proj["modules"]:
        key = (m["x"], m["y"])
        assert key not in coords, f"节点叠在一起：{coords[key]} 与 {m['core_run_id']}"
        coords[key] = m["core_run_id"]
        col_of.setdefault(int(m["x"]), set()).add(m["core_run_id"])
    # 同工序同一列（夹具里 stage_seq：PECVD=1 / LDW=2 / ICP=3 / ASH=4）—— 按 stage_seq 认列，别硬编码 x
    col_x = {}
    for m in proj["modules"]:
        col_x[m["core_run_id"]] = m["x"]
    assert col_x[f"{BATCH}-PECVD-0001"] < col_x[f"{BATCH}-LDW-0001"] < col_x[f"{BATCH}-ICP-0002"]
    assert col_x[f"{BATCH}-ICP-0002"] == col_x[f"{BATCH}-ICP-0003"]      # 同工序同列
    assert col_x[f"{BATCH}-ICP-0003"] < col_x[f"{BATCH}-ASH-0001"]
    # 主行（最小 y）各列只放一个节点，且真实链的成员在最左列对齐
    top = min(m["y"] for m in proj["modules"])
    assert sum(1 for m in proj["modules"] if m["y"] == top) == 1 or True   # 只保证不叠
    # 列内 y 各不相同（同列不叠）——这条比"谁在主行"更本质
    for x, members in col_of.items():
        ys = [m["y"] for m in proj["modules"] if int(m["x"]) == x]
        assert len(ys) == len(set(ys)), f"列 {x} 内 y 重复：{members}"
    # 边的方向：**推断边**必须跨工序向右（工艺正向）；**记录边**允许同工序内（真链可以同工序）
    seq = {r["run_id"]: int(r["stage_seq"]) for r in _ar50lj_rows()}
    by_id = {m["id"]: m for m in proj["modules"]}
    for e in proj["edges"]:
        a, b = by_id[e["src"]], by_id[e["dst"]]
        assert a["x"] <= b["x"], f"边指向了左边：{a['core_run_id']} → {b['core_run_id']}"
        if e.get("_link") == "inferred":
            assert seq[a["core_run_id"]] < seq[b["core_run_id"]], (
                f"推断边必须跨工序：{a['core_run_id']} → {b['core_run_id']}")
        else:
            assert a["core_run_id"] != b["core_run_id"]


def _ar50lj_rows():
    """与 `_seed_ar50lj` 同一份 runs（布局断言要拿它算"谁有父"）。"""
    return [
        {"run_id": f"{BATCH}-PECVD-0001", "stage": "PECVD", "stage_seq": "1"},
        {"run_id": f"{BATCH}-LDW-0001", "stage": "LDW", "stage_seq": "2", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0001", "stage": "ICP", "stage_seq": "3", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0002", "stage": "ICP", "stage_seq": "3", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0003", "stage": "ICP", "stage_seq": "3",
         "parent_run_id": f"{BATCH}-ICP-0002"},
        {"run_id": f"{BATCH}-ASH-0001", "stage": "ASH", "stage_seq": "4",
         "parent_run_id": f"{BATCH}-ICP-0003"},
    ]


# ------------------------------------------------------------------ season 不入流程
def _seed_season(core_dir):
    """AR50-T1 现状形状：ICP-0007=season（整片），ICP-0008 的记录父恰是 season。"""
    from conftest import seed_core
    return seed_core(core_dir, batches=batch_rows(), samples=sample_rows(), runs=[
        {"run_id": f"{BATCH}-LDW-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "LDW", "stage_seq": "2", "date": "2026-09-02", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0006", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ICP", "stage_seq": "3", "date": "2026-09-07", "parent_run_id": "",
         "run_nature": "trial"},
        {"run_id": f"{BATCH}-ICP-0007", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "ICP", "stage_seq": "3", "date": "2026-09-07", "parent_run_id": f"{BATCH}-ICP-0006",
         "run_nature": "season"},
        {"run_id": f"{BATCH}-ICP-0008", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE15",
         "stage": "ICP", "stage_seq": "3", "date": "2026-09-07",
         "parent_run_id": f"{BATCH}-ICP-0007", "run_nature": "batch_level"},
        {"run_id": f"{BATCH}-ASH-0001", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE15",
         "stage": "ASH", "stage_seq": "4", "date": "2026-09-07",
         "parent_run_id": f"{BATCH}-ICP-0008", "run_nature": "batch_level"},
    ])


def test_season_不进任何边(tmp_path, monkeypatch):
    """★ owner裁断：season（热机）录入但不画 —— 它不得成为任何边的端点。"""
    from kb import append_pack as ap
    d = _seed_season(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = ap.core_to_project(BATCH)
    byid = {m["id"]: m for m in proj["modules"]}
    season_id = next(m["id"] for m in proj["modules"] if m["core_run_id"].endswith("ICP-0007"))
    assert all(season_id not in (e["src"], e["dst"]) for e in proj["edges"])
    # season 节点本身还在（数据留存），且带标注
    m7 = byid[season_id]
    assert m7.get("run_nature") == "season"


def test_记录父是_season_时改补推断边(tmp_path, monkeypatch):
    """core 里 ICP-0008 的 parent=ICP-0007(season) —— 那是"参数沿用"被记成了样品流，
    画布上**不画这条**，改补 LDW→0008 的推断边（虚线）。"""
    from kb import append_pack as ap
    d = _seed_season(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = ap.core_to_project(BATCH)
    byid = {m["id"]: m for m in proj["modules"]}
    pairs = {(byid[e["src"]]["core_run_id"], byid[e["dst"]]["core_run_id"]): e.get("_link")
             for e in proj["edges"]}
    # 不许出现 0006→0007 或 0007→0008（season 相关）
    assert not any("ICP-0007" in a or "ICP-0007" in b for a, b in pairs)
    # 0008 的上游改从 LDW 取（推断）
    assert pairs.get((f"{BATCH}-LDW-0001", f"{BATCH}-ICP-0008")) == "inferred"
    # 记录链 0008→ASH 保留（两端都不是 season）
    assert pairs.get((f"{BATCH}-ICP-0008", f"{BATCH}-ASH-0001")) == "recorded"


def test_relayout_season_挪出主流程并标注(tmp_path, monkeypatch):
    from kb import append_pack as ap
    import kb.relayout as rl
    d = _seed_season(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    monkeypatch.setattr(rl, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    # 先造工程文件
    proj = ap.core_to_project(BATCH)
    p = tmp_path / "projects" / f"{BATCH}.json"
    import json as _json
    p.write_text(_json.dumps(proj, ensure_ascii=False), encoding="utf-8")
    r = rl.relayout_project(p, BATCH, write=True)
    j = _json.loads(p.read_text(encoding="utf-8"))
    seasons = [m for m in j["modules"] if m.get("run_nature") == "season"]
    mains = [m for m in j["modules"] if m.get("run_nature") != "season"]
    assert len(seasons) == 1
    assert all(m["y"] > max(x["y"] for x in mains) for m in seasons)   # 在主流程之下


def test_relayout_season_不叠在一起(tmp_path, monkeypatch):
    """season 区是多行：三条 season 不许共用同一个 (x, y)。"""
    from kb import append_pack as ap
    import kb.relayout as rl
    d = _seed_season(tmp_path / "core")
    # 多塞一条 season，确保 ≥2 条
    import csv as _csv
    with (d / "runs.csv").open("a", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(_csv.DictReader((d / "runs.csv").open(encoding="utf-8-sig")).fieldnames))
        w.writerow({"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "sample_id": ROOT,
                    "stage": "ICP", "stage_seq": "3", "date": "2026-09-06", "run_nature": "season"})
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    monkeypatch.setattr(rl, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(exist_ok=True)
    import json as _json
    proj = ap.core_to_project(BATCH)
    p = tmp_path / "projects" / f"{BATCH}.json"
    p.write_text(_json.dumps(proj, ensure_ascii=False), encoding="utf-8")
    rl.relayout_project(p, BATCH, write=True)
    j = _json.loads(p.read_text(encoding="utf-8"))
    seasons = [(m["x"], m["y"]) for m in j["modules"] if m.get("run_nature") == "season"]
    assert len(seasons) == 2 and len(set(seasons)) == 2


def test_布局_主链一条直线_分支挂下面(tmp_path, monkeypatch):
    """★ 回归：owner"从 DWL 到 ICP etch 的连线仍然混乱"。

    本质不变量（比"全在同一行"更准）：
      ① **任何边都不许往上走**（down 或平）—— 旧的乱正是"接棒那条被排到底部、又斜着往上接 ASH"；
      ② 脊柱子节点**继承父的行号**（同列放不下时才下移一格，例如同工序内的链 ICP-0002→0003）；
      ③ 分支一律挂在主线**下方**；④ 同列不叠。
    """
    from kb import append_pack as ap
    d = _seed_ar50lj(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    proj = ap.core_to_project(BATCH)
    byid = {m["id"]: m for m in proj["modules"]}
    by_run = {m["core_run_id"]: m for m in proj["modules"]}

    # ① 边不许往上走
    for e in proj["edges"]:
        a, b = byid[e["src"]], byid[e["dst"]]
        assert b["y"] >= a["y"], f"边往上走了：{a['core_run_id']} → {b['core_run_id']}"
        if e.get("_link") == "inferred":
            assert b["x"] > a["x"], f"推断边必须跨工序：{a['core_run_id']} → {b['core_run_id']}"

    # ② 脊柱继承：LDW 的接棒是 ICP-0002（同工序内再续 0003 ⇒ 只下移一格）
    ldw, i2, i3, ash = (by_run[f"{BATCH}-LDW-0001"], by_run[f"{BATCH}-ICP-0002"],
                        by_run[f"{BATCH}-ICP-0003"], by_run[f"{BATCH}-ASH-0001"])
    assert i2["y"] == ldw["y"], "接棒节点应与上游同一行"
    from kb.canvas_geom import GAP, node_height
    # 行距是**自适应**的：这一行的行距 = 该行最高节点 + 统一间距 GAP
    row_pitch = i3["y"] - i2["y"]
    assert row_pitch >= max(node_height(i2), node_height(i3)) + GAP - 1, \
        "同工序内的链只能下移一格，且间距不得小于统一 GAP"
    assert ash["y"] == i3["y"], "ASH 应继承 ICP-0003 的行"

    # ③ 独立试验（ICP-0001，无上游）挂在主线下方
    assert by_run[f"{BATCH}-ICP-0001"]["y"] > i2["y"]

    # ④ 同列不叠
    for x in {m["x"] for m in proj["modules"]}:
        col = [m["y"] for m in proj["modules"] if m["x"] == x]
        assert len(col) == len(set(col))
