"""插件契约（T2 代码扩展点 v0.1）—— 以「菜单/配方解析器」为第一个插件点的回归网。

背景（`docs/extension-points.md`）：平台覆盖不了所有工艺设备 ⇒ 必须有代码扩展点，
但**口径闸留中心**、插件只许产出。第一个插件点就是已经存在但"暗着"的菜单解析器
（`OPENNANO_MENU_PARSER` 指向外部脚本）。本文件钉住它的契约：

  · 无 manifest ⇒ 按 legacy 载入但**标出来**（向后兼容，不假装规矩）；
  · `api_version` 不符 / `kind` 不认识 / **`writable=True`** ⇒ **拒装**（出声）；
  · 缺必需 API ⇒ 拒装并点名缺哪个；
  · 加载失败/拒装**不许静默降级**，而且要能从 `plugin_status()` 读出来。
"""
from __future__ import annotations

import pytest

from kb import menu_reader as mr

VALID = '''\
PLUGIN = {"name": "demo-menu", "version": "0.3", "kind": "menu_parser",
          "api_version": 1, "capabilities": ["parse_menu"]}

def parse_grp(p):
    return []

def parse_rcp(p):
    return []
'''


def _write(tmp_path, body: str):
    p = tmp_path / "fake_menu.py"
    p.write_text(body, encoding="utf-8")
    return p


def _load(tmp_path, monkeypatch, body: str):
    p = _write(tmp_path, body)
    monkeypatch.setenv("OPENNANO_MENU_PARSER", str(p))
    mr._PARSER = None                 # 夹具复位（conftest 也会清，这里显式）
    return p


def test_合规插件可载入且状态可读(tmp_path, monkeypatch):
    _load(tmp_path, monkeypatch, VALID)
    mod = mr.parser()
    assert callable(mod.parse_grp) and callable(mod.parse_rcp)
    st = mr.plugin_status()
    assert st["ok"] is True and st["name"] == "demo-menu" and st["api_version"] == 1
    assert st["manifest"] is True


def test_无_manifest_按_legacy_载入但标记出来(tmp_path, monkeypatch):
    """向后兼容：老式"暗插件"仍能跑，但状态里必须写明缺 manifest（债可见）。"""
    _load(tmp_path, monkeypatch, "def parse_grp(p):\n    return []\n\n"
                                 "def parse_rcp(p):\n    return []\n")
    mr.parser()                                  # 不抛
    st = mr.plugin_status()
    assert st["ok"] is True and st["manifest"] is False
    assert "manifest" in st["error"]


def test_api_版本不符要拒装(tmp_path, monkeypatch):
    _load(tmp_path, monkeypatch, VALID.replace('"api_version": 1', '"api_version": 99'))
    with pytest.raises(mr.MenuParserUnavailable) as e:
        mr.parser()
    assert "api_version" in str(e.value)
    assert mr.plugin_status()["ok"] is False        # 失败要**读得出来**，不是静默


def test_kind_不认识要拒装(tmp_path, monkeypatch):
    _load(tmp_path, monkeypatch, VALID.replace('"menu_parser"', '"teleporter"'))
    with pytest.raises(mr.MenuParserUnavailable):
        mr.parser()


def test_writable_true_一律拒装(tmp_path, monkeypatch):
    """红线做成字段：插件声明可写 ⇒ 拒装（不许它直接写数据资产）。"""
    _load(tmp_path, monkeypatch, VALID.replace('"capabilities"', '"writable": True, "capabilities"'))
    with pytest.raises(mr.MenuParserUnavailable) as e:
        mr.parser()
    assert "writable" in str(e.value)


def test_缺必需_api_要拒装并点名(tmp_path, monkeypatch):
    _load(tmp_path, monkeypatch, VALID.replace("def parse_rcp(p):\n    return []\n", ""))
    with pytest.raises(mr.MenuParserUnavailable) as e:
        mr.parser()
    assert "parse_rcp" in str(e.value)


def test_模块本身报错_不静默降级(tmp_path, monkeypatch):
    _load(tmp_path, monkeypatch, "raise RuntimeError('插件自爆')\n")
    with pytest.raises(mr.MenuParserUnavailable) as e:
        mr.parser()
    assert "插件自爆" in str(e.value)
    assert mr.plugin_status()["ok"] is False


def test_找不到插件文件_状态里说清怎么修(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_MENU_PARSER", str(tmp_path / "nope.py"))
    mr._PARSER = None
    st = mr.plugin_status()                          # 永不抛
    assert st["ok"] is False and "OPENNANO_MENU_PARSER" in st["error"]
