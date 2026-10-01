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


def test_doe_dir_单一权威位_新位不在要出声(monkeypatch, tmp_path, capsys):
    """`_doe_dir()` **不许静默**回退到 33 侧旧软链（工单 `20261001-工艺线-to-工具线-01`）。

    三态都要钉住：
      ① 新位在 ⇒ 用它（静默）；
      ② 新位不在、但工作区在本机 ⇒ **出声**（stderr 说清"扫表会得到 0 张表"），且**仍返回新位**；
      ③ 工作区都不在这台机器（CI / 公网 clone）⇒ 不吵。

    仍能抓住：有人把"旧位兜底"加回来（那样新位缺失时**不报错**、指向一条待拆的软链，
    失败形态正是最难查的"扫表得 0 张"——2026-09-12 E1 同款）。
    """
    import opennano_config as cfg

    try:
        # ③ 工作区不在本机 ⇒ 不吵
        monkeypatch.setenv("OPENNANO_WORKSPACE", str(tmp_path / "no-ws"))
        mod = importlib.reload(cfg)
        assert not mod.DOE_DIR.exists()
        assert "执行表目录不存在" not in capsys.readouterr().err

        # ② 工作区在、新位不在、**旧位（待拆软链）还在** ⇒ 出声，且**不许**回退到 33 侧
        ws = tmp_path / "ws"
        monkeypatch.setenv("OPENNANO_WORKSPACE", str(ws))
        mod = importlib.reload(cfg)           # 先重载，拿到 ws 下的 PERSONAL 常量
        mod.PERSONAL.mkdir(parents=True)      # 造出工作区根（用常量，不写目录名字面量）
        old_side = (mod.PERSONAL / "33_工艺资料" / "干法刻蚀" / "数据科学"
                    / "DOE设计" / "执行表_2026-08-20")
        old_side.mkdir(parents=True)          # ← 关键：把"旧位还在"这个真实前提造出来
        (old_side / "假的执行表.xlsx").write_text("", encoding="utf-8")
        mod = importlib.reload(cfg)           # 再载一次：工作区在、新位不在 ⇒ 应出声
        err = capsys.readouterr().err
        assert "执行表目录不存在" in err and "0 张表" in err, err
        assert "33_工艺资料" not in str(mod.DOE_DIR), "又回退到 33 侧软链了"
        assert mod.DOE_DIR.name == "07_执行表"

        # ① 新位在 ⇒ 用它，且不吵
        (mod.PERSONAL / "32_工艺数据资产" / "07_执行表").mkdir(parents=True)
        mod = importlib.reload(cfg)
        assert mod.DOE_DIR.name == "07_执行表" and mod.DOE_DIR.is_dir()
        assert "执行表目录不存在" not in capsys.readouterr().err
    finally:
        monkeypatch.delenv("OPENNANO_WORKSPACE", raising=False)
        importlib.reload(cfg)          # 还原真实配置，别把临时值留给后面的用例


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
