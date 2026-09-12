"""跨线指针校验（`kb/pointer_check.py`）—— 回归网。

为什么这个用例必须存在（2026-09-13 的教训）：
    UI 里写了「权威判定在数据线 `data_qa.py`」—— **那个文件根本不存在**。
    数据线指出：**UI 里出现不存在的文件，比没有提示更糟**（用户会去 grep 一个查不到的名字）。
    手工记路径必然出错 ⇒ 让机器核，且要核到**符号级**（文件在 ≠ 我引用的那个函数在）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import REPO


def test_指针集合本身可信():
    """14 条 + 每条都指向工作区里真实路径（本机有真源时才要求全命中）。"""
    from kb.pointer_check import CROSSLINE_POINTERS, FORBIDDEN
    assert len(CROSSLINE_POINTERS) == 14
    assert "data_qa.py" in FORBIDDEN                     # 错名必须留在禁用表里
    # 数据线自己的产物路径不许"发明"：ingest/ 与 core/ 下的名字都要以真名为准
    for name in CROSSLINE_POINTERS:
        assert not name.startswith("/") and "…" not in name
        assert re.match(r"^[\w\u4e00-\u9fff./-]+\.(py|csv|md)$", name), name


def test_禁用名不得回流到工具代码():
    """把 2026-09-13 的错名钉死：工具代码里再出现 `data_qa.py` 就直接红。"""
    from kb.pointer_check import FORBIDDEN, check
    res = check(verbose=False)
    assert res["forbidden_hits"] == [], (
        "工具代码里残留了不存在的文件名；改回真源："
        + json.dumps(res["forbidden_hits"], ensure_ascii=False)
    )
    assert "data_qa.py" in FORBIDDEN


def test_符号级校验_引用的函数必须真在真源里(ws_root):
    """**文件在 ≠ 我引用的函数在** —— 逐条核符号（这是错指针的真正教训）。"""
    syms = [
        # (文件, 期望形式, 符号, 我在哪引用)
        ("个人空间/18_工艺数据资产/03_实验数据/ingest/core_schema.py", "def", "qa",
         "batch_events.consistency_preview 的 authority 文案（QA 关 [11]）"),
        ("个人空间/18_工艺数据资产/03_实验数据/ingest/build_core.py", "call", "qa",
         "同上：qa() **由 build_core 调用**（不是它定义的）、计入违约"),
        ("个人空间/18_工艺数据资产/03_实验数据/ingest/datasets_menu.py", "def", "parse_grp",
         "menu_reader 动态加载的 .grp 解析入口"),
        ("个人空间/18_工艺数据资产/03_实验数据/ingest/datasets_menu.py", "def", "parse_rcp",
         "menu_reader 动态加载的 .rcp 解析入口"),
    ]
    missing = []
    for rel, form, sym, why in syms:
        p = ws_root / rel
        if not p.exists():
            pytest.skip(f"本机没有真源：{p}")
        txt = p.read_text(encoding="utf-8", errors="ignore")
        pat = rf"^def {sym}\b" if form == "def" else rf"(?<![\w.]){sym}\("
        if not re.search(pat, txt, re.M):
            missing.append(f"{rel} 里没有 {form} {sym}（{why}）")
    assert missing == [], "\n".join(missing)


def test_check_在真工作区全命中():
    from kb.pointer_check import check
    res = check(verbose=False)
    if res["missing"]:
        pytest.skip("本机缺部分跨线真源：" + "、".join(m["name"] for m in res["missing"]))
    assert res["ok"] is True and res["present"] == res["checked"] == 14


def test_manifest_给数据线的清单结构():
    """清单要能被数据线脚本直接消费：每条带 `exists`，且写明**双向承诺**。"""
    from kb.pointer_check import CROSSLINE_POINTERS, manifest_obj
    man = manifest_obj()
    assert man["kind"] == "opennano-crossline-pointers" and man["version"] == 1
    assert len(man["pointers"]) == len(CROSSLINE_POINTERS) == man["check"]["checked"]
    assert all(set(p) == {"name", "path", "exists"} for p in man["pointers"])
    assert [p["name"] for p in man["pointers"]] == sorted(p["name"] for p in man["pointers"])
    assert "承诺不擅自改名" in man["rule"] and "只读" in man["rule"]
    assert man["forbidden"] and man["check"]["ok"] in (True, False)


def test_工具不写_core_也不写_ingest():
    """零号铁律的静态守卫：工具代码里不许出现对 core/ingest 的**写**操作。"""
    kb = REPO / "kb"
    bad = []
    write_pat = re.compile(r"(open\([^)]*['\"][wa]|\.write_text\(|\.write_bytes\(|"
                           r"to_csv\(|writestr\()")
    for py in sorted(kb.glob("*.py")):
        txt = py.read_text(encoding="utf-8", errors="ignore")
        for m in write_pat.finditer(txt):
            line = txt[max(0, m.start() - 200):m.start()]
            # 只关心"写到数据资产"的目标；临时目录/内存/提案目录是允许的
            if any(t in line for t in ("core/", "core\"", "ingest/", "CORE_DIR",
                                       "core_dir()", "events_path()", "core_runs_path()")):
                bad.append(f"{py.name}: {m.group(0)}")
    assert bad == [], "工具代码里疑有写数据资产的动作：" + "；".join(bad)
