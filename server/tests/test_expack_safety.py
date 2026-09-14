"""实验包解包的**安全**判据（2026-09-13 审计发现后新建）。

⚠️ **先纠正一个容易想当然的结论（实测过，见下）**：`zipfile.extractall` 在 CPython 上**自带清理** ——
`../outside/x` 会被改写成 dest 内的 `outside/x`、`/abs` 剥成 `abs`、符号链接成员被还原成**普通文件**。
所以在**这版 Python 上经典 Zip Slip/符号链接逃逸并不可利用**（第一版报告说"会真的写出去"是错的）。
真正成立、也确实修掉了的是这四条：
  1. **zip 炸弹 / 资源耗尽**：没有任何体积与成员数上限 ⇒ 一个高压缩比包就能写满磁盘；
  2. **静默改写路径**：`../runs.csv` 被悄悄改成 `runs.csv` ⇒ 可与包内同名成员**互相覆盖**，
     且用户完全不知道（显式拒绝 > 静默改写）；
  3. **解包目录从不清理**：每次导入 zip 都在系统临时目录漏一个 `expack_*`；
  4. **坏包在 API 层漏成 500**：应是 400 带原因（用户看不出哪里坏了）。

本文件只测"**不该发生的事不会发生**"与"正常包不受影响"，不碰真 core、全部落 `tmp_path`。
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from kb import expack as ex


def _zip_with(members: dict[str, bytes], *, symlink: str = "") -> Path:
    """造一个 zip（返回路径）；`symlink` 非空时额外加一个指向它的符号链接成员。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (0o120777 << 16)          # S_IFLNK
            z.writestr(zi, "/etc/passwd")
    out = Path(tempfile.mkdtemp()) / "pkg.zip"
    out.write_bytes(buf.getvalue())
    return out


# ------------------------------------------------------------------ Zip Slip

def test_relative_traversal_is_rejected_and_writes_nothing_outside(tmp_path):
    """`../` 成员：**显式拒绝**（旧行为是 `extractall` 静默改成 dest 内的同名路径 ⇒ 可能覆盖他人）。

    断言两件事：①抛出可读错误（不再静默改写）②解包目录外一个字节都没动
    —— 第②条在旧代码下也成立（`extractall` 自带清理），它守的是"以后换实现也别退化成真逃逸"。
    """
    victim = tmp_path / "victim.txt"
    victim.write_text("原内容", encoding="utf-8")
    pkg = _zip_with({"../victim.txt": b"pwned", "MG/runs.csv": b"run_id\n"})
    with pytest.raises(ex.ExpackError) as ei:
        ex.parse_expack(pkg, None)
    assert "zip slip" in str(ei.value)
    assert victim.read_text(encoding="utf-8") == "原内容"     # 没被覆盖


def test_absolute_member_is_rejected(tmp_path):
    pkg = _zip_with({"/tmp/should_not_exist_xyz.txt": b"x"})
    with pytest.raises(ex.ExpackError) as ei:
        ex.parse_expack(pkg, None)
    assert "绝对路径" in str(ei.value)


def test_symlink_member_is_rejected(tmp_path):
    pkg = _zip_with({"MG/runs.csv": b"run_id\n"}, symlink="MG/link")
    with pytest.raises(ex.ExpackError) as ei:
        ex.parse_expack(pkg, None)
    assert "符号链接" in str(ei.value)


# ------------------------------------------------------------------ 炸弹 / 上限

def test_total_uncompressed_budget_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "MAX_ZIP_BYTES", 1024)            # 用小值验判据，不真造炸弹
    pkg = _zip_with({"MG/big.bin": b"A" * 4096})
    with pytest.raises(ex.ExpackError) as ei:
        ex.parse_expack(pkg, None)
    assert "zip 炸弹" in str(ei.value)


def test_member_count_budget_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "MAX_ZIP_MEMBERS", 3)
    pkg = _zip_with({f"MG/f{i}.txt": b"x" for i in range(6)})
    with pytest.raises(ex.ExpackError) as ei:
        ex.parse_expack(pkg, None)
    assert "成员数超过上限" in str(ei.value)


def test_broken_zip_reports_readable_error(tmp_path):
    bad = tmp_path / "not.zip"
    bad.write_bytes(b"this is not a zip")
    with pytest.raises(ex.ExpackError) as ei:
        ex.parse_expack(bad, None)
    assert "不是有效的 zip" in str(ei.value)


# ------------------------------------------------------------------ 正常包不受影响 + 清理

def test_valid_package_still_imports(tmp_path):
    """**回归锁**：安全校验不能把正常包挡在门外。"""
    csv_head = ("run_id,batch_id,sample_id,stage,stage_seq,date,parent_run_id\n"
                "OK-T1-PECVD-0001,OK-T1,,PECVD,1,2026-09-01,\n")
    pkg = _zip_with({"OK-T1/runs.csv": csv_head.encode(),
                     "OK-T1/manifest.json": b'{"batch_id": "OK-T1"}'})
    proj = ex.parse_expack(pkg, None)
    assert proj["name"] == "OK-T1"
    assert [m["core_run_id"] for m in proj["modules"]] == ["OK-T1-PECVD-0001"]


def test_unpacked_temp_dir_is_cleaned_up(tmp_path):
    """解包目录用完即清（修复前每次导入都在系统临时目录留一个 `expack_*`）。"""
    import glob
    csv_head = ("run_id,batch_id,sample_id,stage,stage_seq,date,parent_run_id\n"
                "OK-T1-PECVD-0001,OK-T1,,PECVD,1,2026-09-01,\n")
    pkg = _zip_with({"OK-T1/runs.csv": csv_head.encode()})
    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "expack_*")))
    ex.parse_expack(pkg, None)
    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "expack_*")))
    assert after - before == set()


def test_bad_zip_leaves_no_temp_dir_behind(tmp_path):
    import glob
    pkg = _zip_with({"../evil.txt": b"x"})
    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "expack_*")))
    with pytest.raises(ex.ExpackError):
        ex.parse_expack(pkg, None)
    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "expack_*")))
    assert after - before == set()


def test_import_endpoint_returns_400_for_a_bad_zip(tmp_path):
    """API 层：坏包要 **400 带原因**（修复前是 500，用户看不出哪里坏了）。"""
    from fastapi.testclient import TestClient
    import main
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"not a zip at all")
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/expack/import", json={"path": str(bad)})
    assert r.status_code == 400, r.text
    assert "不是有效的 zip" in r.json()["detail"]


def test_import_endpoint_returns_404_for_a_missing_path(tmp_path):
    """顺带锁住 404（别因为加了 try 把它也吞成 400）。"""
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/expack/import", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 404
