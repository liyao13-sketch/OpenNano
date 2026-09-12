"""CI 用到的命令行出口 —— 回归网必须能守住"推上去见绿灯"的那几条纪律。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO


def _run(*args, env_extra=None):
    import os
    env = dict(os.environ, **(env_extra or {}))
    return subprocess.run([sys.executable, "-m", "kb.pointer_check", *args],
                          cwd=REPO, capture_output=True, text=True, env=env, timeout=120)


def test_真源齐全时_strict_必须零退出():
    """本机（有真源）跑 `--strict` 必须绿灯 —— 这是 CI 同款命令的另一半。"""
    from kb.pointer_check import check
    if check(verbose=False)["missing"]:
        import pytest
        pytest.skip("本机缺跨线真源（CI 环境）⇒ 由 --allow-missing 那条覆盖")
    r = _run("--strict", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    man = json.loads(r.stdout)
    assert man["check"]["ok"] is True and man["check"]["checked"] == 14


def test_允许缺失时_strict_仍拦禁用名(monkeypatch, tmp_path):
    """`--allow-missing` 只为"别人机器没有数据资产"开的一个口子：
    它**不能**把"工具代码里出现不存在的文件名"一起放过。"""
    from kb import pointer_check as pc
    # ① 直接注入一个禁用名，确认 check() 会抓（不依赖环境）
    (REPO / "kb" / "_probe_forbidden.py").write_text(
        "# 探针：模拟有人把 QA 关指到 data_qa.py\nx = 'data_qa.py'\n", encoding="utf-8")
    try:
        res = pc.check(verbose=False)
        # 探针里 `data_qa.py` 出现两次（注释 + 字符串）⇒ 命中两条；关键是**抓到了这个文件**
        assert {h["file"] for h in res["forbidden_hits"]} == {"_probe_forbidden.py"}
        assert res["ok"] is False
        # ② 命令行出口在 allow-missing 下也必须非零（禁用名 > 缺失豁免）
        r = _run("--strict", "--allow-missing")
        assert r.returncode == 1, r.stdout + r.stderr
    finally:
        (REPO / "kb" / "_probe_forbidden.py").unlink(missing_ok=True)


def test_探针清掉后_check_恢复干净():
    """自检的收尾：探针不能留在仓库里（否则 CI 永远红）。"""
    from kb.pointer_check import check
    assert check(verbose=False)["forbidden_hits"] == []
    assert not list(REPO.glob("kb/_probe_*.py"))


def test_manifest_可落盘且可被数据线脚本消费(tmp_path):
    out = tmp_path / "pointers.json"
    r = _run("--write-manifest", str(out))
    assert r.returncode == 0
    man = json.loads(out.read_text(encoding="utf-8"))
    assert man["kind"] == "opennano-crossline-pointers"
    assert len(man["pointers"]) == 14
    assert all({"name", "path", "exists"} <= set(p) for p in man["pointers"])
