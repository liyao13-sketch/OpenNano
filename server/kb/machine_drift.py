"""机台口径漂移判据 —— **应用库/外部清单的 `tool_id` × core 权威 `TOOL_DISPLAY`**（2026-09-17）。

## 为什么要有这一条（工单 `20260915-助手线-to-兼-01` B2-残C · 工具线半）

工单 `§附 待裁①` 已裁 **(a)**：**`core_schema.TOOL_DISPLAY` 是唯一真相**，数据线把它导出成
`core/machine_tool_display.json`（派生物）；工具线侧有两张**另一层**的表：

  ① `kb/machine_catalog.py` —— 外部机台清单（`~/.opennano/machines.json`，应用库播种用，
     `name` 是**画布名**（"某线-DRIE"这类内部叫法），另有 `tool_id` 指 core 口径）；
  ② `engine/library.py` 的 `_migrate()` 里那张 `tool_id` 回填表（`machines_version < 6`）。

两张表都**手工维护**、都写着 core 的 `tool_id` ⇒ **必然漂移**。漂移的后果不是报错而是
**静默降级**：`resolve_tool()` 见 `cand ∉ TOOL_DISPLAY` 就落哨兵 `UNKNOWN`，
于是 run 被记成"机台未记录"，而应用库里明明写着机台名（`test_unregistered_*` 实测过这条路）。

⚠️ 数据线的闸（`validate_tool_ids` 第 ⑤ 条）**看不见这张表** —— 它只看 `runs.csv`，
即"已经导出的记录"。本判据补的正是**导出之前**那一半：机台档案里写的 `tool_id` 合不合法。

⚠️ **本模块里不许写任何真机台/厂商字面量**（公开层棘轮 `test_public_layer_hygiene.py` 会对账）：
判据只认 `core_vocab.TOOL_DISPLAY` 这张**镜像表**，提示语里也只用「已登记的键 / 撞 stage 的键」这类
**相对表述**，不举真型号当例子。

## 判据（分层，全部**可机器判**）

| 级别 | 码 | 含义 | 处置 |
|---|---|---|---|
| **硬** | `unregistered_tool_id` | `tool_id ∉ TOOL_DISPLAY` | 导出会落哨兵 + 出 `unregistered_machine` 告警 |
| **硬** | `tool_id_is_stage` | `tool_id` 撞 stage 代号（把工序名当机台号） | `resolve_tool` 一律落哨兵 |
| **提示** | `tool_id_is_sentinel` | 机台档案把哨兵 `UNKNOWN` 当机台号填 | 该机台永远显示"机台未记录"，查数时分不出来 |
| **提示** | `tool_id_empty` | 机台有 `tool_id` 字段但为空 | 导出落哨兵 —— **可能是"型号未核实"的有意留空** |
| **提示** | `machine_no_tool_id` | 机台档案**没有** `tool_id` 字段 | 同上（尚未回填的老库） |
| **提示** | `display_name_mismatch` | 档案里的显示文字与权威显示名**互不包含** | 疑似"档案写的是另一台机"（不是硬错，机器无法判） |

**只报不改**：本模块不写任何库、不改任何清单（与 `pointer_check.py` 同一姿势）。
空表**不是通过** —— 见 `status()` 与 `--check` 的三态说明。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import core_vocab

#: `--check` 退出码（沿用数据线 `export_tool_display.py` 的三态约定）
RC_OK, RC_FAIL, RC_EMPTY = 0, 1, 3

#: 判据版号 —— 写进 JSON 报告，方便"判据自己也会变"这件事可追溯
RULE_VERSION = "machine-drift.v1"

#: 档案里"人读显示文字"的字段，按优先级取第一个非空（`model` 是主要落点）
_DISPLAY_FIELDS = ("model", "display", "display_name", "machine_model", "vendor_model")

_HARD = ("unregistered_tool_id", "tool_id_is_stage")


def _norm(s: str) -> str:
    """归一化：小写、去掉非字母数字（中文字符保留）。"""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _strip_bracket(s: str) -> str:
    """去掉尾注括号（`VENDOR MODEL（工艺）` → `VENDOR MODEL`）。"""
    return re.sub(r"[（(][^）)]*[）)]\s*$", "", (s or "").strip()).strip()


def _tokens(s: str) -> set[str]:
    """把显示名切成**可用 token**（≥3 字符的字母数字/中文段）。"""
    return {t for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", _strip_bracket(s)) if len(t) >= 3}


def display_compatible(a: str, b: str) -> bool:
    """两段显示文字是否**像是同一台机**（判据宽，只用来出提示、不当硬错）。

    通过的情形：任一为空 / 归一化后互相包含 / 去括号后互相包含 / **一方有 token 是另一方的子串**。
    实测三种典型：档案只写型号（`M1234` vs `VENDOR M1234（工艺）`）· 空格/大小写不同
    （`AB CdefG` vs `AB Cdef G（RIBE）`）· 档案只写短名（`M6` vs `VENDOR M6（…）`）—— 都算兼容。

    ⚠️ **故意不做"通用厂名表"**（`厂商名` 单靠它不算同一台机）：那张表本身是**内部指纹**，
    而本模块属公开仓库代码（公开层棘轮 `test_public_layer_hygiene.py`）。实测：真库 9 台在
    **没有**该表的情况下全部判为兼容 ⇒ 有它没它都不影响判据效力。
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True                              # 没写显示文字 ⇒ 不判（缺字段另有码）
    if na in nb or nb in na:
        return True
    sa, sb = _norm(_strip_bracket(a)), _norm(_strip_bracket(b))
    if sa and sb and (sa in sb or sb in sa):
        return True
    ta, tb = _tokens(a), _tokens(b)
    return any(x in y or y in x for x in ta for y in tb)


def check_machines(machines: list[dict] | None, *,
                   tool_display: dict[str, str] | None = None,
                   stage_codes=(), source: str = "") -> dict:
    """→ 判据报告（**纯函数**：不读文件、不写文件、不猜）。

    `machines` = 机台档案列表（应用库的 `machines` 或外部清单的 `machines`）；
    `tool_display` 缺省用镜像表 `core_vocab.TOOL_DISPLAY`（跨线是否逐字一致由 G2 判据负责）。
    """
    td = dict(tool_display if tool_display is not None else core_vocab.TOOL_DISPLAY)
    sentinel = core_vocab.TOOL_ID_SENTINEL
    stage_norm = {str(s).strip().upper() for s in (stage_codes or ())}
    unknown_disp = td.get(sentinel, core_vocab.TOOL_UNKNOWN_DISPLAY)

    hard: list[dict] = []
    warnings: list[dict] = []
    for i, m in enumerate(machines or []):
        if not isinstance(m, dict):
            hard.append({"code": "machine_not_object", "index": i,
                         "message": f"第 {i + 1} 项不是对象：{m!r}"})
            continue
        name = str(m.get("name") or "").strip() or f"（第 {i + 1} 项无名）"
        tid = str(m.get("tool_id") or "").strip()

        def _add(bucket, code, message, hint, **extra):
            bucket.append({"code": code, "machine": name, "tool_id": tid,
                           "message": message, "hint": hint, **extra})

        if not tid:
            if "tool_id" in m:
                _add(warnings, "tool_id_empty",
                     f"机台 `{name}` 的 `tool_id` 是空串",
                     "导出会落哨兵 `UNKNOWN`（= 机台未记录）。若这是**有意留空**"
                     "（型号未核实 ⇒ 回填表故意没收录），无需处理；否则请填 core 口径值。")
            else:
                _add(warnings, "machine_no_tool_id",
                     f"机台 `{name}` 档案里没有 `tool_id` 字段",
                     "导出会落哨兵 `UNKNOWN`（= 机台未记录）。⚠️ **可能是有意留空** —— "
                     "`machines_version 6` 的回填表**故意没收录型号未核实的那几台**，"
                     "若本机就是那种情况则无需处理；否则请补 core 口径值。")
            continue

        if tid == sentinel:
            _add(warnings, "tool_id_is_sentinel",
                 f"机台 `{name}` 把哨兵 `{sentinel}` 当机台号填了",
                 "哨兵表示「机台未记录」，不是某台机的编号 ⇒ 该机台在 core 里分不出来。"
                 "请填真机台号，或把该字段留空。")
        elif tid in stage_norm:
            _add(hard, "tool_id_is_stage",
                 f"机台 `{name}` 的 `tool_id='{tid}'` 撞 **stage 代号**",
                 f"`resolve_tool` 见 stage 同名一律落哨兵（「把工序名当机台号」）；"
                 f"请改成 core 口径的真机台号（登记表里的键，通常带厂名/型号前缀）。")
        elif tid not in td:
            _add(hard, "unregistered_tool_id",
                 f"机台 `{name}` 的 `tool_id='{tid}'` **不在 core 的 `TOOL_DISPLAY` 里**",
                 f"`resolve_tool` 会落哨兵 `{sentinel}` 并出 `unregistered_machine` 告警 ⇒ "
                 f"该机的 run 会被记成「机台未记录」。两条路：① 请数据线把它登记进 "
                 f"`core_schema.TOOL_DISPLAY`（并同步本侧镜像）② 把档案里的 `tool_id` 改成已登记的键。")

        # 显示文字一致性（只在 tool_id 已登记时判 —— 未登记时后面的登记问题是主线）
        if tid in td:
            for k in _DISPLAY_FIELDS:
                val = str(m.get(k) or "").strip()
                if not val:
                    continue
                if not display_compatible(val, td[tid]):
                    _add(warnings, "display_name_mismatch",
                         f"机台 `{name}` 的 `{k}='{val}'` 与权威显示名 "
                         f"`{td[tid]}`（`tool_id={tid}`）**互不包含**",
                         "疑似「档案写的是另一台机」或显示名过期；机器判不了，请人核一眼"
                         "（权威显示名只看 core，不看档案）。")
                break                            # 只看最高优先级的那个字段

    return {
        "rule": RULE_VERSION,
        "source": source,
        "authority": "core_vocab.TOOL_DISPLAY（镜像自 core_schema.TOOL_DISPLAY · 权威在数据线）",
        "checked": len(machines or []),
        "hard": hard,
        "warnings": warnings,
        "hard_count": len(hard),
        "warning_count": len(warnings),
        "ok": not hard,
    }


# ---------------------------------------------------------------- 数据来源（只读）

def library_path() -> Path:
    """应用库路径（照 `engine.library` 的同一开关，不 import 它 —— 见下）。"""
    import os
    from opennano_config import LIBRARY_PATH
    return Path(os.environ.get("OPENNANO_LIBRARY") or LIBRARY_PATH)


def load_library_machines(path=None) -> tuple[list[dict] | None, str]:
    """**只读**应用库 JSON 里的 `machines`（→ `(machines, 说明)`；读不到 → `(None, 原因)`）。

    ⚠️ **故意不走 `LibraryStore`**：它的 `__init__` 会跑迁移链、可能**写盘**
    （`_migrate()` 里 `_save()`）—— 判据只该读，不该在体检时改主人的库。
    """
    p = Path(path) if path else library_path()
    if not p.exists():
        return None, f"库文件不存在：{p}"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return None, f"库文件读不动（{type(e).__name__}: {e}）：{p}"
    ms = raw.get("machines") if isinstance(raw, dict) else None
    if not isinstance(ms, list):
        return None, f"库文件里没有 `machines` 列表：{p}"
    return ms, f"{p}（{len(ms)} 台）"


def load_external_machines() -> tuple[list[dict] | None, str]:
    """只读外部清单 `~/.opennano/machines.json`（不存在 → `(None, 说明)`）。"""
    from . import machine_catalog as mc
    p = mc.path()
    if not p.exists():
        return None, f"未提供外部清单：{p}"
    try:
        ms = mc.load()
    except mc.MachineCatalogError as e:
        return None, str(e)
    return ms, f"{p}（{len(ms or [])} 台）"


def status(path=None) -> dict:
    """给 `/api/health` 与排障用（**永不抛**）：本机应用库机台档案的漂移概况。"""
    try:
        ms, src = load_library_machines(path)
    except Exception as e:                          # noqa: BLE001  （健康检查绝不许把服务带红）
        return {"ok": True, "skipped": True, "reason": f"{type(e).__name__}: {e}",
                "hard": 0, "warnings": 0, "messages": []}
    if ms is None:
        return {"ok": True, "skipped": True, "reason": src, "hard": 0, "warnings": 0,
                "messages": []}
    rep = check_machines(ms, source=src)
    return {"ok": rep["ok"], "skipped": False, "reason": "", "source": src,
            "checked": rep["checked"], "hard": rep["hard_count"],
            "warnings": rep["warning_count"],
            "messages": [f["message"] for f in rep["hard"]]}


# ---------------------------------------------------------------- CLI

def _report(rep: dict, src: str) -> None:
    print(f"机台口径漂移判据 {rep['rule']} —— 源：{src}")
    print(f"  权威：{rep['authority']}")
    print(f"  受检机台：{rep['checked']} 台 · 硬 {rep['hard_count']} · 提示 {rep['warning_count']}")
    for level, tag in (("hard", "✗ 硬"), ("warnings", "· 提示")):
        for f in rep[level]:
            print(f"    {tag}  [{f['code']}] {f['message']}")
            print(f"           ↳ {f['hint']}")
    if rep["ok"]:
        print("  → 通过（没有会让导出落哨兵的口径错误）")
    else:
        print("  → **不通过**：上列硬项会让 run 的 `tool_id` 落哨兵 `UNKNOWN`（机台静默丢失）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="机台口径漂移判据：应用库/外部清单的 tool_id × core TOOL_DISPLAY（只读）")
    ap.add_argument("--library", default="", help="应用库路径（默认 OPENNANO_LIBRARY / ~/.opennano/library.json）")
    ap.add_argument("--external", action="store_true", help="改查外部清单 ~/.opennano/machines.json")
    ap.add_argument("--check", action="store_true",
                    help=f"机器可判退出口：通过 {RC_OK} / 有硬项 {RC_FAIL} / 没得查 {RC_EMPTY}")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    a = ap.parse_args(argv)

    if a.external:
        ms, src = load_external_machines()
    else:
        ms, src = load_library_machines(a.library or None)

    if ms is None:
        # **没得查 ≠ 通过**（工单 §五-4 的同一条纪律）
        rep = {"rule": RULE_VERSION, "source": src, "checked": 0, "hard": [], "warnings": [],
               "hard_count": 0, "warning_count": 0, "ok": False, "skipped": True}
        if a.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"[空跑] {src}", file=sys.stderr)
            print("       修法：指定 `--library <路径>`，或先让应用库生成（跑一次服务/`LibraryStore()`）。",
                  file=sys.stderr)
            print(f"       本判据**未执行**（≠ 通过）。退出码 {RC_EMPTY}。", file=sys.stderr)
        return RC_EMPTY if a.check else RC_OK

    rep = check_machines(ms, source=src)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _report(rep, src)
    if a.check and not rep["ok"]:
        return RC_FAIL
    return RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
