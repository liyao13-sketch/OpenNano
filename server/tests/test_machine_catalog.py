"""机台清单外置（T1 数据扩展点）—— `kb/machine_catalog.py` 的回归锁。

口径（本文件钉住）：
  · **没有外部清单 ⇒ 用仓库里的中性样例播种**（`kb/machines.demo.json`，零真机台/厂名）；
  · 有清单 ⇒ 用它播种（新增机台＝改数据，不改仓库代码）；
  · 清单**存在但坏** ⇒ 抛 `MachineCatalogError` 出声，**绝不静默回退**；
  · `status()` 永不抛（给 /api/health 用）。

⚠️ 2026-09-26（工单 `20260915-助手线-to-兼-01` B2-残C 的 C6）：**内建真机台表已整表删除**
（它曾把厂名/型号/内部备注写进公开仓库）⇒ 本文件原先那条「没有清单时机台数 ≥10」的断言
**前提已不存在**，改为钉「回退到中性样例」这件事本身（下面的用例）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.library import LibraryStore
from kb import machine_catalog as mc


def test_没有清单时回退到中性样例(tmp_path, monkeypatch):
    """**没有外部清单 ⇒ 用仓库中性样例播种**（原"内建真机台表"已按工单删除）。

    仍能抓住：① 回退链断了（列表空 ⇒ 新装机台库空）；② 有人又把真机台名/厂名写回公开代码
    （样例机台必须是中性名、且**不许**带 vendor/model —— 那是身份指纹）。
    """
    monkeypatch.delenv("OPENNANO_MACHINES", raising=False)
    monkeypatch.setenv("OPENNANO_MACHINES", str(tmp_path / "nope.json"))
    lib = LibraryStore(tmp_path / "library.json")
    ms = lib.data["machines"]
    assert ms, "没有清单时中性样例播种失效了（新装机会得到空机台库）"
    assert all(m.get("name") for m in ms)
    # 中性性：样例机台名走 `DEMO-*`，且不带厂名/型号（真档案只在 **本机** machines.json）
    demo = [m for m in ms if (m.get("name") or "").startswith("DEMO-")]
    assert len(demo) >= 3, f"样例机台太少：{[m.get('name') for m in ms]}"
    assert all(not m.get("vendor") and not m.get("model") for m in demo), \
        "公开仓库的样例机台不许带厂名/型号（那是真身份指纹）"
    # 表征设备仍由 `_seed_metrology_machines` 追加（共用，不挂工艺模板）
    # ⚠️ 这里**不写那些设备名**：它们也在公开层禁词表里（棘轮只许往下压）——
    #    判据要的语义是"样例机台之外还补了 ≥3 台共用表征设备"，用**结构**表达即可。
    extra = [m for m in ms if not (m.get("name") or "").startswith("DEMO-")]
    assert len(extra) >= 3, f"表征设备没补上：{[m.get('name') for m in ms]}"
    assert all(m.get("notes") for m in extra), "表征设备该带一句用途备注"
    # 样例清单自己必须过 `machine_catalog.validate`（格式与真档案同一套）
    assert mc.validate([{k: v for k, v in m.items() if k in mc.KNOWN_FIELDS} for m in demo]) == []


def test_有清单时按清单播种(tmp_path, monkeypatch):
    cat = tmp_path / "machines.json"
    cat.write_text(json.dumps({"version": 1, "machines": [
        {"name": "机台甲", "equipment_id": "etch_x", "tool_id": "TOOLX",
         "vendor": "厂A", "model": "M1"},
        {"name": "机台乙", "status": "active"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(cat))
    lib = LibraryStore(tmp_path / "library.json")
    names = [m["name"] for m in lib.data["machines"]]
    # 外部清单管辖"电学/工艺机台"这一段（后面 `_seed_metrology_machines` 仍会追加共用表征设备）
    assert names[:2] == ["机台甲", "机台乙"], names
    assert lib.data["machines"][0]["vendor"] == "厂A"
    assert lib.data["machines"][0]["tool_id"] == "TOOLX"


def test_老库升级_字段由外部清单补全(tmp_path, monkeypatch):
    """**老库升级链**：机台档案只有名字的旧库（`machines_version=3`）⇒ 型号/厂家/机台号/备注
    由**外部清单**补上（原先是代码内建表；2026-09-26 · C6 整表外置）。

    仍能抓住：`_enrich_machines` / `_apply_equipment_list_notes` / `machines_version 6` 回填
    改成清单驱动后**不再生效**（老库升级后界面上一片空 —— 这正是那次「迁移链不可达」P0 的同族病）。
    """
    cat = tmp_path / "machines.json"
    cat.write_text(json.dumps({"version": 1, "machines": [
        {"name": "机台甲", "equipment_id": "", "equipment_template": "RIE",
         "tool_id": "DEMO-ETCH-A", "vendor": "厂A", "model": "M1",
         "max_sample": "8 寸", "status": "active", "notes": "清单里的备注"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(cat))
    old = tmp_path / "library.json"
    old.write_text(json.dumps({
        # 老库形状：工艺目录还没重建过（`seed_version` 缺）⇒ 迁移时会先建 equipment，
        # 机台那一段停在上古的 v3（只有名字）
        "version": 1, "machines_version": 3,
        "machines": [{"id": "mc1", "name": "机台甲", "model": "", "serial": "",
                      "equipment_id": "", "location": "", "status": "active", "notes": ""}],
    }, ensure_ascii=False), encoding="utf-8")
    lib = LibraryStore(old)
    m = next(x for x in lib.data["machines"] if x["name"] == "机台甲")
    assert m["vendor"] == "厂A" and m["model"] == "M1", m
    assert m["max_sample"] == "8 寸"
    assert m["tool_id"] == "DEMO-ETCH-A", "v6 的 tool_id 回填没从清单取"
    assert m["notes"] == "清单里的备注", "v5 的备注刷新没从清单取"
    assert m["equipment_id"], "equipment_template→equipment_id 没反查上"


def test_清单坏掉要出声_不静默回退(tmp_path, monkeypatch):
    cat = tmp_path / "machines.json"
    cat.write_text("{这不是合法 json", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(cat))
    with pytest.raises(mc.MachineCatalogError) as e:
        mc.load()
    assert "不会" in str(e.value) or "不回退" in str(e.value).replace("**", "")


def test_清单校验_缺名与重名都要报(tmp_path):
    errs = mc.validate([{"vendor": "x"}, {"name": "A"}, {"name": "A"}])
    assert any("缺 `name`" in e for e in errs)
    assert any("重复" in e for e in errs)
    assert mc.validate([{"name": "A"}]) == []


def test_status_永不抛(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_MACHINES", str(tmp_path / "x.json"))
    st = mc.status()
    assert st["present"] is False and st["ok"] is True      # 没清单 = 正常（用内建表）
    bad = tmp_path / "bad.json"
    bad.write_text("[[[", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(bad))
    st2 = mc.status()
    assert st2["present"] is True and st2["ok"] is False and st2["error"]
