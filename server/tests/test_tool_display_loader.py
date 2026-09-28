"""机台口径**加载器**与跨线判据（`kb/core_vocab.py`）—— 回归网（2026-09-26 · 工单 B2-残C 的 C3/C4）。

钉住四条口径（都是工单 §五 的机器可判条款）：

1. **加载顺序**：`OPENNANO_TOOL_DISPLAY` → 工作区 `<CORE_DIR>/machine_tool_display.json` → 仓库**中性样例**；
2. **失败出声**：显式路径坏 / 真清单存在但坏 ⇒ **抛错**（绝不静默退回样例 —— 否则真机台静默变"未登记"）；
3. **样例不是权威**：`degraded=True` ⇒ 跨线判据报 **跳过（3）**，不是通过；
4. **三态可分**：`compare_with_authority()` 通过 0 / 失败 1 / 跳过 3（与数据线 `--check` 同一套约定）。

⚠️ 这些用例**只读**真源；真源不在（CI/公网 clone）时用合成 JSON，**不静默跳过**（除了要跟真源对拍的那条）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from conftest import WS_ROOT

from kb import core_vocab as cv


def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def _demo_like(**kw):
    """合成一份「格式正确」的清单（**中性名**，不引真机台）。"""
    tool = kw.pop("tool", [{"tool_id": "DEMO-ETCH-A", "display": "Demo Etcher A（样例）"},
                           {"tool_id": cv.TOOL_ID_SENTINEL,
                            "display": cv.TOOL_UNKNOWN_DISPLAY}])
    obj = {"schema": cv.SCHEMA_VERSION, "mode": kw.pop("mode", "enforced"),
           "sentinel": kw.pop("sentinel", cv.TOOL_ID_SENTINEL),
           "sentinel_display": kw.pop("sentinel_display", cv.TOOL_UNKNOWN_DISPLAY),
           "tool": tool}
    obj["sha256"] = cv.entry_digest(tool, obj["sentinel"], obj["sentinel_display"])
    obj.update(kw)
    return obj


# ---------------------------------------------------------------- 校验与摘要

def test_摘要算法与数据线同一份():
    """`entry_digest` 必须与数据线 `ingest/export_tool_display.py::entry_digest` **逐字同算法**。

    为什么：本加载器要能校验**他们生成的真清单**（头部 `sha256`）。算法一漂，真清单会被误判成"被改过"。
    仍能抓住：有人"顺手优化"了归一化/键序/分隔符 ⇒ 与他们的产物对不上 ⇒ 立刻红。
    """
    # ⚠️ 路径走 `opennano_config.DATA_ROOT`：**不硬拼工作区目录名**（那是另一张单的 B1 债，
    #    且公开层棘轮会数它 —— 判据自己也不许写那个词）。
    from opennano_config import DATA_ROOT
    p = Path(DATA_ROOT) / "ingest" / "export_tool_display.py"
    if not p.exists():
        pytest.skip("本机没有数据线的 exporter（评测/CI）⇒ 无法对拍算法（跳过 ≠ 通过）")
    spec = importlib.util.spec_from_file_location("_their_exporter", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tool = [{"tool_id": "DEMO-ETCH-A", "display": "Demo Etcher A（样例）"},
            {"tool_id": cv.TOOL_ID_SENTINEL, "display": cv.TOOL_UNKNOWN_DISPLAY}]
    mine = cv.entry_digest(tool, cv.TOOL_ID_SENTINEL, cv.TOOL_UNKNOWN_DISPLAY)
    theirs = mod.entry_digest({"tool": tool, "sentinel": cv.TOOL_ID_SENTINEL,
                              "sentinel_display": cv.TOOL_UNKNOWN_DISPLAY})
    assert mine == theirs, "两边摘要算法漂了 ⇒ 真清单会被误判"


def test_校验_缺列与重号都要报():
    assert cv.validate({"tool": [{"tool_id": "A"}]}), "缺 display 该报"
    errs = cv.validate({"tool": [{"tool_id": "A", "display": "a"},
                                 {"tool_id": "A", "display": "b"}]})
    assert any("重复" in e for e in errs)
    assert cv.validate(_demo_like()) == []


def test_头部哈希对不上_抛错():
    obj = _demo_like()
    obj["sha256"] = "0" * 64
    with pytest.raises(cv.ToolDisplayError) as e:
        cv.parse(obj, "合成")
    assert "sha256" in str(e.value)


def test_哨兵不一致_出声但不采纳(capsys):
    """清单声明了别的哨兵 ⇒ **告知**，但不改本侧语义常量（哨兵属【跨线】契约）。"""
    obj = _demo_like(sentinel="UNKNOWN-DEMO", sentinel_display="UNKNOWN-DEMO（机台未记录）")
    mapping, mode = cv.parse(obj, "合成")
    err = capsys.readouterr().err
    assert "不采纳" in err and "UNKNOWN-DEMO" in err
    assert cv.TOOL_ID_SENTINEL == "UNKNOWN"            # 语义常量没被清单改掉
    assert mapping["DEMO-ETCH-A"] == "Demo Etcher A（样例）"


# ---------------------------------------------------------------- 加载顺序与失败态

def test_显式路径不存在_抛错而不静默改来源(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_TOOL_DISPLAY", str(tmp_path / "nope.json"))
    with pytest.raises(cv.ToolDisplayError) as e:
        cv.load()
    assert "OPENNANO_TOOL_DISPLAY" in str(e.value)


def test_真清单存在但坏_抛错(tmp_path, monkeypatch):
    bad = _write(tmp_path / "machine_tool_display.json", {"tool": [{"tool_id": "A"}]})
    monkeypatch.delenv("OPENNANO_TOOL_DISPLAY", raising=False)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(tmp_path))
    with pytest.raises(cv.ToolDisplayError):
        cv.load()
    assert bad.exists()


def test_真清单不存在_正常回退到中性样例(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENNANO_TOOL_DISPLAY", raising=False)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(tmp_path / "empty"))
    mapping, info = cv.load()
    assert info["source"] == "demo" and info["degraded"] is True
    assert mapping, "样例不该是空的"
    # 样例里的键必须是**中性名**：`DEMO-*` / `*-DEMO` / 哨兵（不写死真机台名，否则判据自己又成指纹）
    assert all(k == cv.TOOL_ID_SENTINEL or k.startswith("DEMO-") or k.endswith("-DEMO")
               for k in mapping), f"样例清单含非中性键：{list(mapping)}"


def test_显式路径优先于工作区真清单(tmp_path, monkeypatch):
    mine = _write(tmp_path / "mine.json", _demo_like(mode="demo"))
    monkeypatch.setenv("OPENNANO_TOOL_DISPLAY", str(mine))
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(tmp_path / "empty"))
    mapping, info = cv.load()
    assert info["source"] == "env" and str(mine) == info["path"]


# ---------------------------------------------------------------- 三态判据（C4）

def test_样例态报跳过而不是通过(monkeypatch):
    monkeypatch.setitem(cv.TOOL_DISPLAY_INFO, "degraded", True)
    rc, msg = cv.compare_with_authority()
    assert rc == cv.RC_SKIP == 3, msg
    assert "未执行" in msg and "跳过" in msg


def test_权威不可达报跳过(monkeypatch, tmp_path):
    monkeypatch.setitem(cv.TOOL_DISPLAY_INFO, "degraded", False)
    monkeypatch.setattr(cv, "authority_path", lambda: tmp_path / "nope" / "core_schema.py")
    rc, msg = cv.compare_with_authority()
    assert rc == cv.RC_SKIP, msg
    assert "权威源不可达" in msg


def test_不一致报失败(monkeypatch):
    """把本侧表改一条（模拟"清单读串了"）⇒ 判据必须红，且指出是哪一类不一致。"""
    monkeypatch.setitem(cv.TOOL_DISPLAY_INFO, "degraded", False)
    monkeypatch.setattr(cv, "load_authority",
                        lambda: ({"tool": [("AAA", "甲"), ("BBB", "乙")],
                                  "sentinel": cv.TOOL_ID_SENTINEL,
                                  "sentinel_display": cv.TOOL_UNKNOWN_DISPLAY,
                                  "stages": set(), "path": "合成权威"}, "合成权威"))
    monkeypatch.setattr(cv, "TOOL_DISPLAY", {"AAA": "甲", "BBB": "乙他"})
    rc, msg = cv.compare_with_authority()
    assert rc == cv.RC_FAIL == 1, msg
    assert "同键不同名" in msg


def test_顺序不同也算不一致(monkeypatch):
    monkeypatch.setitem(cv.TOOL_DISPLAY_INFO, "degraded", False)
    monkeypatch.setattr(cv, "load_authority",
                        lambda: ({"tool": [("AAA", "甲"), ("BBB", "乙")],
                                  "sentinel": cv.TOOL_ID_SENTINEL,
                                  "sentinel_display": cv.TOOL_UNKNOWN_DISPLAY,
                                  "stages": set(), "path": "合成权威"}, "合成权威"))
    monkeypatch.setattr(cv, "TOOL_DISPLAY", {"BBB": "乙", "AAA": "甲"})
    rc, msg = cv.compare_with_authority()
    assert rc == cv.RC_FAIL and "顺序不同" in msg


def test_与真权威一致时通过():
    """真源在场且本侧用的是真清单 ⇒ **通过**（这是本机常态；CI 上是"跳过"，见上两条）。"""
    if cv.TOOL_DISPLAY_INFO.get("degraded"):
        pytest.skip("本机只加载到中性样例 ⇒ 本条不适用（跳过 ≠ 通过）")
    rc, msg = cv.compare_with_authority()
    assert rc == cv.RC_OK, msg


# ---------------------------------------------------------------- 未登记机台仍落哨兵并出声

def test_未登记机台落哨兵并出声(monkeypatch):
    monkeypatch.setattr(cv, "TOOL_DISPLAY", {"DEMO-ETCH-A": "Demo Etcher A（样例）"})
    tid, name, note = cv.resolve_tool({"machine_name": "画布某台"},
                                      [{"name": "画布某台", "tool_id": "NOT-REGISTERED"}])
    assert tid == cv.TOOL_ID_SENTINEL
    assert name == cv.TOOL_UNKNOWN_DISPLAY
    assert "NOT-REGISTERED" in note and "TOOL_DISPLAY" in note, "必须**出声**，不许静默丢机台名"
