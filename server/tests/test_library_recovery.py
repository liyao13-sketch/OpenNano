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


def test_库文件是原子写的不留半截(tmp_path):
    """2026-09-16 审计 P1：`_save` 改走 `engine/atomic.write_json_atomic`。

    原来裸 `write_text` 整份覆盖 —— 崩在写一半，盘上就是半截 JSON，整个共享资产库报废
    （rev 守卫只防"别人改过"，防不了这个）。这里钉住：写完成功、内容完整、不留临时文件。
    """
    p = _store(tmp_path)
    lib = LibraryStore(p)
    lib.add_machine("M1")
    assert p.exists() and json.loads(p.read_text(encoding="utf-8"))["machines"], "库没落盘/内容不完整"
    leftovers = [f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    assert leftovers == [], f"原子写留下了临时文件：{leftovers}"


def test_资产库路径可由_env_覆盖(tmp_path, monkeypatch):
    """2026-09-16 审计 P2：`OPENNANO_LIBRARY` 可覆盖 —— 不许再写死在模块里。

    口径与 `opennano_config` 其它项一致（**在 import 时读环境**）。
    """
    import importlib
    monkeypatch.setenv("OPENNANO_LIBRARY", str(tmp_path / "custom.json"))
    import opennano_config as cfg
    from engine import library as lib_mod
    importlib.reload(cfg)
    importlib.reload(lib_mod)
    try:
        assert str(lib_mod.DEFAULT_PATH).endswith("custom.json")
    finally:                                  # 复原，别污染同进程后续用例
        monkeypatch.delenv("OPENNANO_LIBRARY", raising=False)
        importlib.reload(cfg)
        importlib.reload(lib_mod)


# ---------------------------------------------------------------- 播种/迁移链（2026-09-16 审计 P0）
def test_全新安装必须播种并落盘(tmp_path):
    """红证：修复前全新库是**空骨架**（无工艺目录/无机台/无参数），而且**不落盘**。

    根因：整段播种+迁移链被缩进事故塞进了 `_quarantine_corrupt()` 的末尾 ⇒
    只有"库文件损坏"那条路才会跑。新同事机器/新服务器/CI 全部中招。
    """
    p = _store(tmp_path)
    lib = LibraryStore(p)
    assert (lib.data.get("equipment") or {}), "全新库没有设备库（工艺目录没播种）"
    assert len(lib.data.get("machines") or []) >= 10, "全新库没有机台"
    assert lib.data.get("params"), "全新库没有默认参数"
    assert lib.data.get("seed_version") == 7 and lib.data.get("machines_version") == 7
    assert p.exists(), "播种后没落盘 ⇒ 下次启动又是空的"
    # 型号/厂家是"迁移链顺序 bug"的直接受害者：<6 挡在 <4 前面 ⇒ enrich 永不执行。
    # ⚠️ 这里**不写具体机台名**（真机台名是公开层指纹，判据会红）—— 只看结构。
    assert any(m.get("vendor") and m.get("model") for m in lib.data["machines"]), \
        "机台型号/厂家没补（顺序 bug：<6 挡在 <4 前面）"


def test_老库载入要被迁移(tmp_path):
    """合法 JSON 但很旧（无 seed_version / 无机台）⇒ 必须被迁移，而不是"载入即完事"。"""
    p = _store(tmp_path)
    p.write_text(json.dumps({"version": 1, "params": {"膜厚": {"unit": "nm", "category": "膜厚"}}},
                            ensure_ascii=False), encoding="utf-8")
    lib = LibraryStore(p)
    assert lib.data.get("seed_version") == 7, "老库没被迁移（迁移链不可达）"
    assert lib.data["params"].get("膜厚"), "迁移把用户已有的参数弄丢了"
    assert len(lib.data.get("machines") or []) >= 10


def test_v6_库要补做_enrich(tmp_path):
    """已到 v6 但 `<4` 从未跑过的库（顺序 bug 的受害者）⇒ v7 补做，且**只填空字段**。

    构造方式：先播种出新库、把机台字段人为抹空、版本退回 6 —— 这样源码里
    **不必写任何真机台名**（机器名只作为运行期数据出现）。
    """
    p = _store(tmp_path)
    fresh = LibraryStore(p)
    ms = [dict(m) for m in fresh.data["machines"]]
    assert ms, "播种没出机台，用例前提不成立"
    for m in ms:
        m["vendor"], m["model"] = "", ""
    ms[0]["notes"] = "**用户手改的备注**"
    ms[0]["vendor"] = "某厂"                       # 已填值：绝不许被覆盖
    data = dict(fresh.data)
    data["machines"] = ms
    data["machines_version"] = 6                    # 退回受害版本
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    lib = LibraryStore(p)
    got = {m["name"]: m for m in lib.data["machines"]}
    assert sum(1 for m in lib.data["machines"] if m.get("vendor")) > 1, "v7 没补上缺的厂家"
    assert got[ms[0]["name"]]["notes"] == "**用户手改的备注**", "补做覆盖了用户手改的备注"
    assert got[ms[0]["name"]]["vendor"] == "某厂", "补做覆盖了已填值"
    assert lib.data["machines_version"] == 7


def test_迁移是幂等的(tmp_path):
    """同一份库连续载入两次，数据不许被"迁移两遍"污染（影响规则/依赖边都是列表）。"""
    p = _store(tmp_path)
    a = LibraryStore(p)
    first = json.dumps(a.data, ensure_ascii=False, sort_keys=True)
    b = LibraryStore(p)
    second = json.dumps(b.data, ensure_ascii=False, sort_keys=True)
    assert first == second, "第二次载入又改动了库内容 ⇒ 迁移不幂等"
