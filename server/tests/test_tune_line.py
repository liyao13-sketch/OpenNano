"""参数调试线（`kb/tune_line.py`）—— 回归网。

数据侧在 `core/process.db` 建了 `v_tune_line` 视图（缺 = NULL、不补值）；
工具侧**直接查它**，并把"哪列可比 / 哪轮全空"如实报出来。
"""
from __future__ import annotations

import sqlite3

import pytest

from batch_fixtures import BATCH

COLS = ["tune_id", "tune_step", "run_id", "date", "stage", "tool", "sample_id",
        "t_set_s", "t_dwell_s", "source_w", "bias_w", "bias_w_actual",
        "chf3_sccm", "ar_sccm", "o2_sccm", "cf4_sccm", "sf6_sccm",
        "cd_delta_nm", "depth_nm", "er_nm_min", "selectivity",
        "film_thickness_nm", "stress_mpa", "refractive_index"]

ROWS = [
    # TUNE1 四轮（照 AR50-T1 现状：step2 只有 depth、step3 全空）
    (f"{BATCH}-ICP-TUNE1", "1", f"{BATCH}-ICP-0002", "2026-09-06", "ICP", "PishowA", f"{BATCH}-01-DIE4",
     300, None, 750, 150, None, 45, 20, None, None, None, "639", None, None, None, None, None, None),
    (f"{BATCH}-ICP-TUNE1", "2", f"{BATCH}-ICP-0003", "2026-09-06", "ICP", "PishowA", f"{BATCH}-01-DIE4",
     300, None, 752, 205, None, 45, 20, None, None, None, None, "888.8", None, None, None, None, None),
    (f"{BATCH}-ICP-TUNE1", "3", f"{BATCH}-ICP-0005", "2026-09-07", "ICP", "PishowA", f"{BATCH}-01-DIE4",
     240, None, 749, 222, None, 45, 20, None, None, None, None, None, None, None, None, None, None),
    (f"{BATCH}-ICP-TUNE1", "4", f"{BATCH}-ICP-0006", "2026-09-07", "ICP", "PishowA", f"{BATCH}-01-DIE4",
     240, None, 793, 223, None, 45, 20, None, None, None, "114", None, "252", None, None, None, None),
    # 另一条扫描（只有 2 轮，都在 PECVD）
    (f"{BATCH}-PECVD-TUNE1", "1", f"{BATCH}-PECVD-0001", "2026-09-01", "PECVD", "PECVD-1", f"{BATCH}-01",
     None, None, None, None, None, None, None, None, None, None, None, None, None, None, "899.7", "-35.2", "1.4623"),
    (f"{BATCH}-PECVD-TUNE1", "2", f"{BATCH}-PECVD-0002", "2026-09-02", "PECVD", "PECVD-1", f"{BATCH}-01",
     None, None, None, None, None, None, None, None, None, None, None, None, None, None, "905.1", "-33.0", "1.4619"),
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """造一个带 `v_tune_line` 视图的合成 process.db（列名照数据线真视图）。"""
    from kb import batch_runs as br
    core = tmp_path / "core"
    core.mkdir(exist_ok=True)                 # autouse 夹具已建过同名目录
    (core / "runs.csv").write_text("run_id,batch_id\n", encoding="utf-8")   # 只为让 core_runs_path 成立
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    for name in dir(br):
        if name.endswith("_CACHE"):
            setattr(br, name, None)
    db = core / "process.db"
    con = sqlite3.connect(db)
    coldef = ", ".join(f'"{c}" TEXT' for c in COLS)
    con.execute(f'CREATE VIEW v_tune_line AS SELECT {coldef} FROM (SELECT NULL AS "_x") WHERE 0')
    # 视图不可写 ⇒ 用表 + 同名视图替换：先建临时表插数，再重建视图为 SELECT *
    con.execute("DROP VIEW v_tune_line")
    con.execute(f'CREATE TABLE _tune_src ({coldef})')
    con.executemany(f"INSERT INTO _tune_src VALUES ({','.join('?' * len(COLS))})", ROWS)
    con.execute("CREATE VIEW v_tune_line AS SELECT * FROM _tune_src")
    con.commit()
    con.close()
    return db


def test_按_tune_id_分组且步序正确(db):
    from kb.tune_line import tune_lines
    r = tune_lines(BATCH)
    assert r["available"] is True
    ids = [s["tune_id"] for s in r["series"]]
    assert ids == [f"{BATCH}-ICP-TUNE1", f"{BATCH}-PECVD-TUNE1"]
    s1 = r["series"][0]
    assert [s["tune_step"] for s in s1["steps"]] == ["1", "2", "3", "4"]
    assert s1["n_steps"] == 4
    assert s1["stage"] == "ICP" and s1["sample_id"] == f"{BATCH}-01-DIE4"


def test_可比性只报不猜(db):
    """`cd_delta_nm` 有 2 个点 ⇒ 可比；`depth_nm` 只有 1 个 ⇒ 不算可比；step3 全空要点名。"""
    from kb.tune_line import tune_lines
    s = tune_lines(BATCH)["series"][0]
    assert s["response_avail"] == {"cd_delta_nm": 2, "depth_nm": 1, "er_nm_min": 1}
    assert s["comparable_responses"] == {"cd_delta_nm": 2}
    assert "响应可比 1/3" in s["comparability_note"]
    assert "step 3" in s["comparability_note"]
    assert "可拟合：cd_delta_nm" in s["comparability_note"]


def test_空值就是空值_绝不补(db):
    from kb.tune_line import tune_lines
    s = tune_lines(BATCH)["series"][0]
    step3 = s["steps"][2]
    assert all(step3[c] is None for c in
               ["cd_delta_nm", "depth_nm", "er_nm_min", "selectivity"])
    # 参数列也不许顺手补
    assert s["steps"][0]["t_dwell_s"] is None


def test_全批次与过滤(db):
    from kb.tune_line import tune_lines
    assert len(tune_lines("")["series"]) == 2
    only = tune_lines(BATCH)["series"]
    assert {s["tune_id"] for s in only} == {f"{BATCH}-ICP-TUNE1", f"{BATCH}-PECVD-TUNE1"}
    none = tune_lines("NO-SUCH")
    assert none["available"] is True and none["series"] == []


def test_没有_视图或库时_明说而不是_500(tmp_path, monkeypatch):
    from kb import batch_runs as br
    from kb.tune_line import tune_lines
    core = tmp_path / "core2"
    core.mkdir()
    (core / "runs.csv").write_text("run_id\n", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    for name in dir(br):
        if name.endswith("_CACHE"):
            setattr(br, name, None)
    r = tune_lines(BATCH)                     # 连 process.db 都没有
    assert r["available"] is False and "build_core.py" in r["reason"]
    db = core / "process.db"
    con = sqlite3.connect(db)
    con.execute("create table dummy(x)")
    con.close()
    r2 = tune_lines(BATCH)                    # 有库没视图
    assert r2["available"] is False and "v_tune_line" in r2["reason"]
