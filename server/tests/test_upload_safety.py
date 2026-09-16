"""上传端点安全（`/api/kb/ingest_upload`）—— 2026-09-16 审计 P0 的回归网。

红证（修前实测）：`tmp_dir / req.filename` 未消毒 ——
  · `filename` 给**绝对路径**时 pathlib 直接丢弃 `tmp_dir`（`Path("/tmp/x") / "/a/b"` → `/a/b`）
  · `../` 同样逃逸
  ⇒ 任何登录成员可传 `filename=~/.opennano/users.json` 的绝对路径 + 任意 content 覆写账号库。
修复：只取文件名部分（剥掉一切路径成分）+ 解码后大小上限 + 临时目录必清理。
"""
from __future__ import annotations

import base64
import glob
import os
import tempfile

import pytest

pytest.importorskip("fastapi", reason="接口用例需要 fastapi")
from fastapi.testclient import TestClient            # noqa: E402


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def test_filename_绝对路径不逃逸(client, tmp_path):
    """P0 红→绿：绝对路径形态不得写出临时目录（探针路径必须不存在）。"""
    probe = tmp_path / "p0_probe" / "evil.xlsx"
    payload = {"filename": str(probe), "content_b64": _b64(b"not-a-real-xlsx"),
               "process_type": "RIE_Cl", "dry_run": True}
    r = client.post("/api/kb/ingest_upload", json=payload)
    # 解析会失败（不是真 xlsx），但**绝不许**在探针路径落盘
    assert not probe.exists(), "filename 绝对路径逃逸出临时目录 = P0 复发"
    assert not probe.parent.exists()


def test_filename_dotdot不逃逸(client, tmp_path):
    probe_dir = tmp_path / "escape"
    payload = {"filename": f"../{probe_dir.name}/evil.xlsx",
               "content_b64": _b64(b"not-a-real-xlsx"),
               "process_type": "RIE_Cl", "dry_run": True}
    # 把"逃逸目标"对准系统临时根下的相对位置：若按原名写入，会落到 <tmp>/escape/
    cwd = os.getcwd()
    os.chdir(tempfile.gettempdir())
    try:
        r = client.post("/api/kb/ingest_upload", json=payload)
    finally:
        os.chdir(cwd)
    assert not os.path.exists(
        os.path.join(tempfile.gettempdir(), probe_dir.name, "evil.xlsx")), \
        "filename ../ 逃逸 = P0 复发"
    assert r.status_code == 422                  # 不是真 xlsx ⇒ 422，但绝不许写出去


def test_临时目录必清理(client):
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "opennano_up_*")))
    client.post("/api/kb/ingest_upload",
                json={"filename": "x.xlsx", "content_b64": _b64(b"junk"),
                      "process_type": "RIE_Cl", "dry_run": True})
    client.post("/api/kb/ingest_upload",
                json={"filename": "y.xlsx", "content_b64": _b64(b"junk"),
                      "process_type": "RIE_Cl", "dry_run": False})
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "opennano_up_*")))
    assert after == before, f"临时目录泄漏：{sorted(after - before)}"


def test_超限拒绝_413(client, monkeypatch):
    import main as m
    monkeypatch.setattr(m, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/api/kb/ingest_upload",
                    json={"filename": "big.xlsx",
                          "content_b64": _b64(b"x" * 100),
                          "process_type": "RIE_Cl", "dry_run": True})
    assert r.status_code == 413


def test_非法base64_422(client):
    r = client.post("/api/kb/ingest_upload",
                    json={"filename": "x.xlsx", "content_b64": "!!!不是base64!!!",
                          "process_type": "RIE_Cl", "dry_run": True})
    assert r.status_code == 422
