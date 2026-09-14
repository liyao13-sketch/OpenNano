"""数据桥的**属性 / 边界**判据（2026-09-13 审计自查后新建）。

守三件在数据域最要紧、又最容易悄悄破的事：
  1. **导出幂等** —— 同一个画布项目连导两次，run/step/meas 必须逐字一致（第一次调用会给模块补
     `core_run_id`，第二次不能因此重编号、也不能重复追加行）；
  2. **core 只读** —— 调完一整轮"批次视图 + 包导出 + 包导入"之后，core 目录里的文件**逐字节不变**
     （三铁律第一条：CSV 权威、不直写 db、工具侧不写 core）；
  3. **名字不能穿出工程目录** —— `/api/project` 的 `name` 参数做过清洗，但这是"安全靠约定"的典型
     位置，必须有判据钉住（`../` 一律落回 PROJECTS_DIR 内）。

全部落 `tmp_path`，不碰真 core / 真工程目录。
"""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from conftest import seed_core

BATCH = "PROP-T1"


def _project() -> dict:
    mods = [
        {"id": "m1", "equipment_name": "PECVD", "params": {}, "param_outputs": ["膜厚"]},
        {"id": "m2", "equipment_name": "ICP Etch", "params": {}, "param_outputs": ["硅CD"]},
        {"id": "m3", "equipment_name": "扫描电镜（SEM）", "params": {}, "param_outputs": []},
    ]
    edges = [{"src": "m1", "dst": "m2", "_link": "recorded"},
             {"src": "m2", "dst": "m3", "_link": "recorded"}]
    return {"name": BATCH, "modules": mods, "edges": edges}


# ------------------------------------------------------------------ ① 幂等

def test_extract_rows_is_idempotent_on_the_same_project_dict():
    """连导两次：行内容逐字一致（第一次会补 `core_run_id`，第二次不许重编号/重复追加）。"""
    from kb.expack import extract_rows
    proj = _project()
    a = extract_rows(proj)
    b = extract_rows(proj)
    assert a[0] == b[0], "runs 行漂移"
    assert a[1] == b[1], "steps 行漂移"
    assert a[2] == b[2], "measurements 行漂移"
    # 再导一次也不该多出行（防"每次调用 append 到模块上的列表"这类写法）
    assert len(a[0]) == len(b[0]) == 3 and len(a[1]) == len(b[1]) and len(a[2]) == len(b[2])


def test_build_expack_is_byte_stable_across_two_runs():
    """同一个项目导两次包：除日期外，三张 CSV 必须逐字一致（卡/CSV 同源的机器表现）。"""
    from kb.expack import build_expack
    z1, _ = build_expack(_project())
    z2, _ = build_expack(_project())

    def pick(blob, suffix):
        z = zipfile.ZipFile(io.BytesIO(blob))
        name = next(n for n in z.namelist() if n.endswith(suffix))
        return z.read(name).decode("utf-8")

    for suffix in ("runs.csv", "steps.csv", "measurements.csv"):
        assert pick(z1, suffix) == pick(z2, suffix), f"{suffix} 两次导出不一致"


def test_card_and_csv_share_the_same_ids():
    """流程卡（md）与 CSV **同一套 run/step/meas id** —— 卡片里出现的 id 必须都能在 CSV 里找到。"""
    from kb.expack import build_expack, build_process_card
    proj = _project()
    card = build_process_card(proj)
    blob, _ = build_expack(_project())
    z = zipfile.ZipFile(io.BytesIO(blob))
    runs = z.read(next(n for n in z.namelist() if n.endswith("runs.csv"))).decode()
    run_ids = [r["run_id"] for r in csv.DictReader(io.StringIO(runs))]
    assert len(run_ids) == 3
    for rid in run_ids:
        assert rid in card, f"卡上找不到 {rid}"
    # 反向：卡里不该出现 CSV 里没有的 run 号（`PROP-T1-XXX-000n` 形态）
    import re
    for tok in set(re.findall(rf"{BATCH}-[A-Z]+-\d{{4}}", card)):
        assert tok in run_ids, f"卡上的 {tok} 在 CSV 里不存在"


# ------------------------------------------------------------------ ② core 只读

def _snapshot(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_core_is_byte_identical_after_a_full_round_trip(tmp_path, monkeypatch):
    """批次视图 + 导出包 + 导入包 走一圈后，core **逐字节不变**。"""
    from kb import append_pack as ap
    from kb import batch_runs as br
    from kb import expack as ex
    d = seed_core(tmp_path / "core", runs=[
        {"run_id": f"{BATCH}-PECVD-0001", "batch_id": BATCH, "stage": "PECVD",
         "stage_seq": "1", "date": "2026-09-01", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "stage": "ICP",
         "stage_seq": "2", "date": "2026-09-02", "parent_run_id": f"{BATCH}-PECVD-0001"},
    ])
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    for mod in (ap, br, ex):
        if hasattr(mod, "CORE_DIR"):
            monkeypatch.setattr(mod, "CORE_DIR", d, raising=False)

    before = _snapshot(d)
    proj = _project()
    br.chain_of(proj["modules"], BATCH)          # 批次视图（只读）
    blob, _ = ex.build_expack(proj)              # 导出包
    z = tmp_path / "pkg.zip"
    z.write_bytes(blob)
    ex.parse_expack(z, None)                     # 再导回来
    assert _snapshot(d) == before, "core 被改动了 —— 违反三铁律第一条"


# ------------------------------------------------------------------ ③ 路径不穿出

def test_project_name_cannot_escape_the_projects_dir(tmp_path, monkeypatch):
    """`/api/project` 的 name 做了清洗；这条判据钉住"清洗真的有效"（不许靠约定）。"""
    import main
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    base = (tmp_path / "projects").resolve()
    for evil in ("../escape", "../../etc/passwd", "/abs/path", "a/../../b", "..", "."):
        p = main._project_path(evil)
        # 真正的不变量是"**落在工程目录内**"：文件名里带点号没问题（`AR50-T1...json` 也合法），
        # 只要没有路径分隔符、解析后父目录仍是 PROJECTS_DIR 就穿不出去。
        # ⚠️ 第一版我写的是 `assert ".." not in p.name` —— 把正常名字也判成违规（判据自己说谎）。
        assert os.sep not in p.name and "/" not in p.name, f"{evil!r} 生成了带路径分隔符的名字：{p.name}"
        assert p.resolve().parent == base, f"{evil!r} 逃出了工程目录：{p.resolve()}"


def test_bad_requests_are_4xx_not_500(tmp_path):
    """最常见的三种错输入 ⇒ 4xx 带原因（不要漏成 500：用户看不出哪里坏了）。"""
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app, raise_server_exceptions=False)
    r1 = c.post("/api/batch/runs", json={})                       # 缺必填 batch_id
    assert r1.status_code == 422 and r1.json().get("detail")
    r2 = c.post("/api/expack/import", json={"path": str(tmp_path / "nope")})
    assert r2.status_code == 404
    r3 = c.post("/api/layout/arrange", json={"modules": "not-a-list"})
    assert 400 <= r3.status_code < 500
