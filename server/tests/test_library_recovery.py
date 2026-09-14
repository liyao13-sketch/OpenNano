"""库文件损坏时的**处置**判据（2026-09-13 审计发现后新建）。

背景（真实数据丢失路径，已复现）：`~/.opennano/library.json` 是用户的**真资产**
（机台表 / 参数注册表 / 参数依赖边 / 影响规则）。旧实现里：
  · `LibraryStore._load()` 抓到异常后**静默 `pass`**（`self.data` 只剩默认值 + 播种）；
  · `LibraryStore._save()` **无条件覆盖**该文件，而 `__init__` 的播种/迁移路径就会调它。
⇒ **库文件一旦损坏，下次启动就把用户的资产整份换成默认值**，且界面上只表现为
  "机台/模板/影响规则都没了"，没有任何提示 —— 这既是丢数据，也是静默失败。

修后的口径（本文件钉住）：
  1. 损坏文件**原样改名留档**（`library.corrupt-<时间戳>.json`，字节不变、可救回）；
  2. 本次运行**禁止写库**（`save_blocked`），宁可这次不落盘也不覆盖；
  3. 原因与留档路径**可读地暴露**（`load_error` / `corrupt_backup`，并经 `/api/library` 给界面）；
  4. 正常库文件的行为**一点不变**（回归锁）。

⚠️ 全部落 `tmp_path`，绝不碰真的 `~/.opennano/library.json`。
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.library import LibraryStore


def _store(tmp_path: Path) -> Path:
    return tmp_path / "library.json"


def test_corrupt_library_is_preserved_and_not_overwritten(tmp_path):
    """**核心断言**：损坏文件的字节必须原样留存（旧代码下它会被默认值覆盖掉）。"""
    p = _store(tmp_path)
    broken = b'{"params": {"\xe8\x83\xb6CD": '            # 截断的 JSON（真实损坏形态）
    p.write_bytes(broken)

    lib = LibraryStore(p)

    assert lib.load_error, "损坏了却没说原因"
    assert lib.save_blocked is True, "损坏后仍允许写库 ⇒ 会把用户资产换成默认值"
    backup = Path(lib.corrupt_backup)
    assert backup.exists(), "损坏文件没有被留档"
    assert backup.read_bytes() == broken, "留档内容与原文不一致（救不回来了）"
    assert not p.exists(), "原路径被写上了新内容"


def test_corrupt_library_does_not_get_replaced_by_defaults(tmp_path):
    """触发一次写库（播种/迁移路径就会调）后，磁盘上仍**不能**出现"默认值库"。"""
    p = _store(tmp_path)
    p.write_bytes(b"not json at all")
    lib = LibraryStore(p)

    lib._save()                                   # 任何一次写库尝试
    assert not p.exists(), "损坏后仍写出了库文件 ⇒ 用户资产被默认值顶掉"
    lib.add_machine("X")                          # 走一层公开 API，确保内部也走 _save
    assert not p.exists(), "公开写接口绕过了 save_blocked"


def test_library_state_is_exposed_for_the_ui(tmp_path):
    """API 层要能把它说出来（界面才有机会提示）。"""
    import main
    from engine.library import LibraryStore as LS
    p = _store(tmp_path)
    p.write_bytes(b"{ broken")
    old = main.LIB
    try:
        main.LIB = LS(p)
        d = main.api_library()
        assert d["load_error"] and d["save_blocked"] is True
        assert d["corrupt_backup"].endswith(".json")
    finally:
        main.LIB = old


def test_healthy_library_behaves_exactly_as_before(tmp_path):
    """**回归锁**：正常库文件不许被这套逻辑影响（不留档、不拦写、能落盘）。"""
    p = _store(tmp_path)
    p.write_text(json.dumps({"params": {"膜厚": {"unit": "nm", "category": "膜厚"}}},
                            ensure_ascii=False), encoding="utf-8")
    lib = LibraryStore(p)

    assert lib.load_error == "" and lib.save_blocked is False and lib.corrupt_backup == ""
    assert "膜厚" in lib.data["params"], "正常库没被读进来"
    lib.add_machine("M1")
    assert p.exists(), "正常库反而不写盘了"
    assert json.loads(p.read_text(encoding="utf-8"))["params"].get("膜厚")
