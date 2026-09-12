"""跨线**指针校验**（只读）—— 防止"工具里引用了一个不存在的文件"。

为什么要有这个（2026-09-13 的教训）：
    我在 UI 里写了「权威判定在数据线 `data_qa.py`」——**那个文件不存在**，
    真名是 `ingest/core_schema.py` 的 `qa()`。数据线指出：**UI 里出现不存在的文件比没有提示更糟**
    （用户会去 grep 一个查不到的名字）。手工记路径必然出错 ⇒ 让机器核。

用法：
    python3 kb/pointer_check.py            # 只报告
    python3 kb/pointer_check.py --strict   # 有失效即退出码 1（可挂进回归/CI）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .menu_reader import _workspace

#: 工具代码里引用到的**跨线文件**（真源）→ 相对工作区的路径
CROSSLINE_POINTERS = {
    # 数据线：core / ingest（真源，权威在 ingest/*.py 而非 core/*.csv）
    "ingest/core_schema.py":   "个人空间/18_工艺数据资产/03_实验数据/ingest/core_schema.py",
    "ingest/build_core.py":    "个人空间/18_工艺数据资产/03_实验数据/ingest/build_core.py",
    "ingest/datasets_menu.py": "个人空间/18_工艺数据资产/03_实验数据/ingest/datasets_menu.py",
    "ingest/datasets_folder.py": "个人空间/18_工艺数据资产/03_实验数据/ingest/datasets_folder.py",
    "ingest/datasets_eq.py":   "个人空间/18_工艺数据资产/03_实验数据/ingest/datasets_eq.py",
    "ingest/propose_apply.py": "个人空间/18_工艺数据资产/03_实验数据/ingest/propose_apply.py",
    "ingest/batch_events.csv": "个人空间/18_工艺数据资产/03_实验数据/ingest/batch_events.csv",
    "ingest/eq_state_log.csv": "个人空间/18_工艺数据资产/03_实验数据/ingest/eq_state_log.csv",
    "schema_v0.1.md":          "个人空间/18_工艺数据资产/03_实验数据/schema_v0.1.md",
    "现象受控词表.csv":         "个人空间/18_工艺数据资产/03_实验数据/现象受控词表.csv",
    "core/runs.csv":           "个人空间/18_工艺数据资产/03_实验数据/core/runs.csv",
    "core/samples.csv":        "个人空间/18_工艺数据资产/03_实验数据/core/samples.csv",
    # 契约（工具线维护，但路径同样会外发）
    "契约/知识条目Schema与录入规范_v0.2_20260912.md":
        "个人空间/19_工艺资料/契约/知识条目Schema与录入规范_v0.2_20260912.md",
    "契约/实验数据包_规范_v0.1.md":
        "个人空间/19_工艺资料/契约/实验数据包_规范_v0.1.md",
}

#: 明确**不该再出现**的错名（防回潮）
FORBIDDEN = {
    "data_qa.py": "该文件不存在；QA 关在 ingest/core_schema.py 的 qa()（由 build_core.py 运行）",
}


def check(verbose: bool = True) -> dict:
    ws = _workspace()
    missing, present = [], []
    for name, rel in CROSSLINE_POINTERS.items():
        (present if (ws / rel).exists() else missing).append({"name": name, "path": rel})
    # 扫工具代码里是否残留错名
    kb = Path(__file__).resolve().parent
    hit_forbidden = []
    for py in list(kb.glob("*.py")) + [kb.parent / "main.py"]:
        if py.name == Path(__file__).name:      # 校验器自身含"禁用词表"，不扫自己
            continue
        try:
            txt = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for bad, why in FORBIDDEN.items():
            for m in re.finditer(re.escape(bad), txt):
                hit_forbidden.append({"file": py.name, "token": bad, "why": why,
                                      "line": txt[:m.start()].count("\n") + 1})
    res = {"checked": len(CROSSLINE_POINTERS), "missing": missing,
           "present": len(present), "forbidden_hits": hit_forbidden,
           "ok": not missing and not hit_forbidden, "workspace": str(ws)}
    if verbose:
        print(f"跨线指针校验：{res['present']}/{res['checked']} 命中")
        for m in missing:
            print(f"  ✗ 失效：{m['name']}  →  {m['path']}")
        for h in hit_forbidden:
            print(f"  ✗ 禁用名残留：{h['file']}:{h['line']} `{h['token']}` —— {h['why']}")
        if res["ok"]:
            print("  ✅ 全部有效，且无禁用名残留")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="跨线指针校验（只读）")
    ap.add_argument("--strict", action="store_true", help="有失效即退出码 1")
    a = ap.parse_args()
    r = check()
    return 1 if (a.strict and not r["ok"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
