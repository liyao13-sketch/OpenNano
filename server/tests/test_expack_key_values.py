"""`key_values` → `measurements.csv`（2026-09-16 审计 P1 的回归锁）。

红证（修前，子代理实测）：回灌/导入进来的实测值只落在画布 `key_values`
（`_meas_of` 经 `QUANTITY_TO_PARAM` 反写成画布键），而导出侧只读 `core_measurements` /
`param_outputs` ⇒ 真 core AR50-T1 回灌的 **27 个实测值，再导出 measurements.csv 一行不剩**。

口径（本文件钉住）：
  · 受控量名（§三 英文量名）才折 ⇒ 能落回 core；
  · 画布中间量（`size_nm`/`gds_bias` 之流）**不许**当测量写出去；
  · 空值＝未测，不写行；
  · 检测节点上的量折到**它测的那条 run**（host_rid），不是检测 run 自己（协议 §15.1）。
"""
from __future__ import annotations


def _mod(mid, stage, rid, **kw):
    m = {"id": mid, "name": stage, "subtype": "etch", "core_run_id": rid,
         "core_stage": stage, "core_sample_id": "S1",
         # ⚠️ `resolve_stage` 是按**画布模板名**认工序的（它不看 `core_stage`）——
         #    所以检测节点在这里必须给真模板名，否则会被大类兜底成 etch（见 §G.70 末条）。
         "equipment_name": {"RIE": "RIE", "SEM": "扫描电镜（SEM）"}.get(stage, stage)}
    m.update(kw)
    return m


def _meas(rows):
    return [r for r in rows]


def test_回灌的_key_values_要折进_measurements():
    from kb import expack
    m = _mod("m1", "RIE", "E-T1-RIE-0001",
             key_values={"刻蚀深度": 520.0, "选择比": 12.5, "size_nm": 500, "gds_bias": 20})
    rows, _, meas, _, _ = expack.extract_rows({"name": "E-T1", "edges": [], "modules": [m]}, lib=None)
    got = {r[3]: r[4] for r in meas}
    # 受控量名照折（画布中文参数名 → §三 英文量名）
    assert got.get("depth_center_nm") == "520.0", got
    assert got.get("selectivity") == "12.5", got
    # 画布中间量绝不写进 core
    assert "size_nm" not in got and "gds_bias" not in got, got


def test_空值不写行():
    from kb import expack
    m = _mod("m1", "RIE", "E-T1-RIE-0001",
             key_values={"刻蚀深度": "", "选择比": None, "膜厚": 0.0})
    _, _, meas, _, _ = expack.extract_rows({"name": "E-T1", "edges": [], "modules": [m]}, lib=None)
    got = {r[3]: r[4] for r in meas}
    assert "depth_center_nm" not in got and "selectivity" not in got, got
    assert got.get("film_thickness_nm") == "0.0", "0 是有效实测值，不许当空丢掉"


def test_表单填过的量不重复写():
    from kb import expack
    m = _mod("m1", "RIE", "E-T1-RIE-0001",
             key_values={"刻蚀深度": 111.0},
             core_measurements=[{"quantity": "depth_center_nm", "value": "520", "unit": "nm"}])
    _, _, meas, _, _ = expack.extract_rows({"name": "E-T1", "edges": [], "modules": [m]}, lib=None)
    depths = [r for r in meas if r[3] == "depth_center_nm"]
    assert len(depths) == 1 and depths[0][4] == "520", depths


def test_检测节点的量折到被测_run():
    """协议 §15.1：检测 run 上不许挂 measurement ⇒ 折出来的行也必须挂 host（被测 run）。"""
    from kb import expack
    host = _mod("h1", "RIE", "E-T1-RIE-0001")
    sem = _mod("m2", "SEM", "E-T1-SEM-0001",
               core_parent_run_id="E-T1-RIE-0001", key_values={"侧壁角_光栅": 89.2})
    _, _, meas, _, _ = expack.extract_rows(
        {"name": "E-T1", "edges": [], "modules": [host, sem]}, lib=None)
    swa = [r for r in meas if r[3] == "sidewall_angle_deg"]
    assert swa and swa[0][1] == "E-T1-RIE-0001", swa
