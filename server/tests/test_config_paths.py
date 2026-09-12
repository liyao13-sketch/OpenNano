"""本机路径必须**可被环境变量重定向**（`opennano_config.py`）—— 回归网。

为什么值得钉住（2026-09-13 真人真事）：
旧的冒烟脚本 `server/test_api.py` 把工程存进了 `~/.opennano/projects/`，
而界面「载入工程」取的是**最近修改的那个** ⇒ 它存了个 `t.json`，
**owner正在看的 AR50-T1 画布当场被顶掉**（界面显示"项目: t · 1 节点"）。
根因是「用户数据目录」写死在 4 个文件里（main / relayout / repair_edges / store），
测试与脚本无从回避 ⇒ 现在统一收到 `opennano_config`，并支持：

    OPENNANO_PROJECTS_DIR   画布工程目录（默认 ~/.opennano/projects）
    OPENNANO_DB             知识库 SQLite（默认 ~/.opennano/opennano.db）
"""
from __future__ import annotations

import importlib
from pathlib import Path


def test_用户数据目录可被环境变量重定向(monkeypatch, tmp_path):
    import opennano_config as cfg

    monkeypatch.setenv("OPENNANO_PROJECTS_DIR", str(tmp_path / "p"))
    monkeypatch.setenv("OPENNANO_DB", str(tmp_path / "kb.db"))
    mod = importlib.reload(cfg)
    try:
        assert Path(mod.PROJECTS_DIR) == tmp_path / "p"
        assert Path(mod.DB_PATH) == tmp_path / "kb.db"
    finally:
        # 把模块恢复成"默认值"，别把临时值留给后面的用例
        monkeypatch.delenv("OPENNANO_PROJECTS_DIR")
        monkeypatch.delenv("OPENNANO_DB")
        importlib.reload(cfg)


def test_默认值仍在_家目录下_且四个引用处同源():
    """默认路径不变（`~/.opennano/...`），且 main / store / relayout / repair_edges 都不再自己写死。"""
    import opennano_config as cfg

    assert Path(cfg.PROJECTS_DIR) == Path.home() / ".opennano" / "projects"
    assert Path(cfg.DB_PATH) == Path.home() / ".opennano" / "opennano.db"

    repo = Path(__file__).resolve().parents[1]
    for rel in ("main.py", "kb/store.py", "kb/relayout.py", "kb/repair_edges.py"):
        src = (repo / rel).read_text(encoding="utf-8")
        assert 'Path.home() / ".opennano"' not in src, f"{rel} 里又出现了写死的用户数据目录"


def test_回归网与冒烟脚本都不许写真实用户目录():
    """`tests/` 与 `server/test_api.py` 里不许出现真实的 `~/.opennano` 写入路径。"""
    repo = Path(__file__).resolve().parents[1]
    smoke = (repo / "test_api.py").read_text(encoding="utf-8")
    assert "OPENNANO_PROJECTS_DIR" in smoke and "OPENNANO_DB" in smoke, \
        "冒烟脚本必须先把工程目录/知识库指到临时目录再 import main"
