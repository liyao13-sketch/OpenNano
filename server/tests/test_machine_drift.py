"""机台口径**漂移判据**（`kb/machine_drift.py`）—— 回归网（2026-09-17 · 工单 B2-残C 工具线半）。

判据要钉住的口径（`07 §G.72`）：

  1. 应用库/外部清单里的 `tool_id` **不在** `TOOL_DISPLAY` ⇒ **硬项**（导出会静默落哨兵）；
  2. `tool_id` 撞 **stage 代号** ⇒ **硬项**（`resolve_tool` 一律落哨兵，"把工序名当机台号"）；
  3. 哨兵 `UNKNOWN` / 空 / 缺字段 ⇒ **提示**（可能是"型号未核实"的有意留空）；
  4. 档案显示文字与权威显示名互不包含 ⇒ **提示**（疑似写了另一台机，机器判不了）；
  5. **只读**：判据跑完，库文件**逐字节没变**（不走 `LibraryStore.__init__` 的迁移链）；
  6. 空表/读不到 ⇒ `--check` 退出码 **3**（跳过 ≠ 通过）。

⚠️ **本文件只用合成机台**（`ETCH-A`/`LITHO-X` 这类），不写真机台/厂商字面量 ——
公开层棘轮（`test_public_layer_hygiene.py`）会对账，且真库只在"只读真源"那一条里**原样**读。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kb import machine_drift as md
from kb.core_vocab import TOOL_ID_SENTINEL

#: 合成权威表（**用它就不用真表**，判据逻辑与真机台名解耦）
TD = {"ETCH-A": "VendorA Etch-A（氯基）", "LITHO-X": "VendorB Litho-X",
      TOOL_ID_SENTINEL: "UNKNOWN（机台未记录）"}
STAGES = ("ETCH", "LITHO", "MA6")


def _check(machines, **kw):
    return md.check_machines(machines, tool_display=TD, stage_codes=STAGES, source="合成", **kw)


# ---------------------------------------------------------------- 硬项

def test_未登记的tool_id是硬项():
    rep = _check([{"name": "画布甲", "tool_id": "NOT-REGISTERED"}])
    assert not rep["ok"] and rep["hard_count"] == 1
    f = rep["hard"][0]
    assert f["code"] == "unregistered_tool_id" and f["machine"] == "画布甲"
    assert "TOOL_DISPLAY" in f["message"] and f["hint"]      # 要有"怎么办"，不能只报错


def test_tool_id撞stage代号是硬项():
    rep = _check([{"name": "画布乙", "tool_id": "MA6"}])       # 机台名恰与 stage 同名
    assert rep["hard_count"] == 1 and rep["hard"][0]["code"] == "tool_id_is_stage"


def test_干净档案零硬项零提示():
    rep = _check([{"name": "画布丙", "tool_id": "ETCH-A", "model": "VendorA Etch-A"},
                  {"name": "画布丁", "tool_id": "LITHO-X"}])   # 没写 model ⇒ 不判显示名
    assert rep["ok"] and rep["hard_count"] == 0 and rep["warning_count"] == 0, rep


# ---------------------------------------------------------------- 提示

def test_哨兵空值缺字段都只出提示():
    rep = _check([{"name": "甲", "tool_id": TOOL_ID_SENTINEL},
                  {"name": "乙", "tool_id": ""},
                  {"name": "丙"}])
    codes = sorted(f["code"] for f in rep["warnings"])
    assert codes == ["machine_no_tool_id", "tool_id_empty", "tool_id_is_sentinel"], codes
    assert rep["ok"], "这三种是**提示**（可能是有意留空），不该当硬错"


def test_显示名互不包含才提示():
    """宽判据：只写型号 / 空格大小写不同 / 短名 —— 都算兼容；写成另一台机才提示。"""
    assert md.display_compatible("ETCH-A", TD["ETCH-A"])
    assert md.display_compatible("etcha", TD["ETCH-A"])
    assert md.display_compatible("SomeBrand X9", "X9（工艺）")
    assert not md.display_compatible("完全不同的机器", TD["ETCH-A"])
    rep = _check([{"name": "戊", "tool_id": "ETCH-A", "model": "完全不同的机器"}])
    assert rep["ok"] and rep["warning_count"] == 1
    assert rep["warnings"][0]["code"] == "display_name_mismatch"


def test_未登记时不重复报显示名():
    """登记问题才是主线：`tool_id` 未登记时不再叠一条显示名提示（免得刷屏、误导）。"""
    rep = _check([{"name": "己", "tool_id": "NOT-REGISTERED", "model": "完全不同的机器"}])
    assert rep["hard_count"] == 1 and rep["warning_count"] == 0


def test_各项不是对象也要出声():
    rep = _check(["这是一个字符串", None])
    assert rep["hard_count"] == 2 and all(f["code"] == "machine_not_object"
                                         for f in rep["hard"])


# ---------------------------------------------------------------- 只读 + 三态

def test_判据只读_库文件逐字节不变(tmp_path):
    """**只读纪律**：判据不许改主人的库（`LibraryStore.__init__` 会跑迁移链、可能写盘 ⇒ 不许走它）。"""
    p = tmp_path / "library.json"
    p.write_text(json.dumps({"machines": [{"name": "甲", "tool_id": "ETCH-A"}],
                             "machines_version": 1}, ensure_ascii=False), encoding="utf-8")
    before = p.read_bytes()
    ms, src = md.load_library_machines(p)
    assert ms and "1 台" in src
    rep = md.check_machines(ms, tool_display=TD, stage_codes=STAGES, source=src)
    assert rep["ok"]
    assert p.read_bytes() == before, "判据动了库文件！"


def test_没有库文件时报跳过而不是通过(tmp_path, capsys):
    """**跳过 ≠ 通过**（工单 §五-4 的同一条纪律）：读不到就退出码 3，且**出声**。"""
    missing = tmp_path / "nope.json"
    rc = md.main(["--library", str(missing), "--check"])
    err = capsys.readouterr().err
    assert rc == md.RC_EMPTY == 3, rc
    assert "未执行" in err and "≠ 通过" in err
    # 不要求 --check 时（人读体检）不该让脚本失败
    assert md.main(["--library", str(missing)]) == md.RC_OK


def test_check有硬项时退出码1(tmp_path, capsys):
    p = tmp_path / "library.json"
    p.write_text(json.dumps({"machines": [{"name": "甲", "tool_id": "NOT-REGISTERED"}]},
                            ensure_ascii=False), encoding="utf-8")
    assert md.main(["--library", str(p), "--check"]) == md.RC_FAIL == 1
    assert "NOT-REGISTERED" in capsys.readouterr().out


def test_status_永不抛(tmp_path, monkeypatch):
    """给 `/api/health` 用：库不存在 / 读不动都只报 skipped，绝不抛。"""
    monkeypatch.setenv("OPENNANO_LIBRARY", str(tmp_path / "none.json"))
    st = md.status()
    assert st["ok"] is True and st["skipped"] is True and st["hard"] == 0
    bad = tmp_path / "bad.json"
    bad.write_text("[[[", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_LIBRARY", str(bad))
    assert md.status()["skipped"] is True


# ---------------------------------------------------------------- 真库（只读 · 别处自动跳过）

def _real_library() -> Path:
    """主人的真应用库（只读；评测/CI 上不存在 ⇒ 跳过）。"""
    p = Path(os.environ.get("OPENNANO_REAL_LIBRARY")
             or (Path.home() / ".opennano" / "library.json"))
    if not p.exists():
        pytest.skip(f"本机没有真应用库：{p}（评测/CI 环境跳过是预期行为）")
    return p


def test_真库机台档案零硬项():
    """真库应当**零硬项**（有硬项＝机器档案里写着 core 不认的机台号 ⇒ 导出会静默落哨兵）。"""
    ms, src = md.load_library_machines(_real_library())
    assert ms, src
    rep = md.check_machines(ms, source=src)          # 用真镜像表（跨线是否逐字由 G2 判据负责）
    assert rep["hard"] == [], "真库出现硬项：\n  - " + "\n  - ".join(
        f"{f['machine']}: {f['message']}" for f in rep["hard"])


def test_真库判据不是空转的_注入错值立刻被抓(tmp_path):
    """**判据自验**（"体检器会说谎"的教训）：把真库**抄一份**、把某台改成未登记值 ⇒ 必须报硬项。

    ⚠️ 抄件写 `tmp_path`，**绝不改真库**；真库那一份同时再验一次零硬项。
    """
    src_path = _real_library()
    before = src_path.read_bytes()
    raw = json.loads(before.decode("utf-8"))
    machines = raw.get("machines") or []
    assert machines, "真库里没有机台，判据没法自验"
    ms_ok, src = md.load_library_machines(src_path)
    assert md.check_machines(ms_ok, source=src)["hard"] == []

    probe = json.loads(json.dumps(machines))          # 深拷贝
    probe[0]["tool_id"] = "ZZ-NOT-A-REAL-TOOL"
    dst = tmp_path / "library.probe.json"
    dst.write_text(json.dumps({"machines": probe}, ensure_ascii=False), encoding="utf-8")
    ms_bad, _ = md.load_library_machines(dst)
    rep = md.check_machines(ms_bad, source=str(dst))
    assert not rep["ok"] and rep["hard_count"] == 1
    assert rep["hard"][0]["code"] == "unregistered_tool_id"
    assert src_path.read_bytes() == before, "真库被改了（判据必须只读）"
