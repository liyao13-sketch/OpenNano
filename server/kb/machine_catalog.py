"""机台清单外置（T1 数据扩展点 · 2026-09-16 · 工单 `20260915-助手线-to-兼` B2-残C 的工具线侧接缝）。

## 为什么

机台名/型号/厂家**写死在代码里**（`engine/library.py` 的播种表、数据线的 `core_schema.TOOL_DISPLAY`），
后果有二：① 公开仓库里带着真实机台指纹（需要一份人工冻结的"禁词上限"才守得住）；
② 换实验室 / 加一台机就得改代码发版。这与 `docs/extension-points.md` 的 **T1 数据扩展**方向相反。

## 边界（重要：本模块**不改权威**）

工单 `§附 待裁①` 要求数据线先裁「谁是权威」：
  · **(a) 代码仍是权威**，工具线只做"导出 → 加载"；
  · **(b) JSON 清单成为权威**，`core_schema.TOOL_DISPLAY` 改为读它。
在那句话落地之前，本模块只提供**接缝**：
  · 有外部清单 ⇒ 用它播种（新增机台＝改数据，不改仓库代码）；
  · 没有 ⇒ **完全按原行为**（用代码内建表），不改变任何现状；
  · 不碰数据线的 `core_schema`（那是裁定方的地盘）。

## 文件格式（`~/.opennano/machines.json`，可用 `OPENNANO_MACHINES` 覆盖）

```json
{"version": 1,
 "machines": [{"name": "…", "equipment_id": "…", "tool_id": "…",
               "vendor": "…", "model": "…", "max_sample": "…",
               "location": "…", "serial": "…", "status": "active", "notes": "…"}]}
```
`: `name` 必填；`tool_id` 要与 core 口径一致（数据线的机台闸会拦未登记名）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: 外部清单位置（**不进仓库**：机台名是身份指纹）
DEFAULT_PATH = Path(os.environ.get("OPENNANO_MACHINES")
                    or (Path.home() / ".opennano" / "machines.json"))

#: 允许的字段（多出来的键保留但不校验；`name` 必填）
KNOWN_FIELDS = ("name", "equipment_id", "tool_id", "vendor", "model", "max_sample",
                "location", "serial", "status", "notes")


class MachineCatalogError(RuntimeError):
    """外部清单存在但读不动/不合法 —— **出声**，绝不静默退回内建表（那会让主人以为清单生效了）。"""


def path() -> Path:
    return Path(os.environ.get("OPENNANO_MACHINES") or DEFAULT_PATH)


def load() -> list[dict] | None:
    """→ 机台列表；**文件不存在返回 None**（= 用内建表，保持原行为）。

    存在但坏 ⇒ 抛 `MachineCatalogError`（带原因与修法），不静默降级。
    """
    p = path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise MachineCatalogError(
            f"外部机台清单读不动/不是合法 JSON：{p}（{type(e).__name__}: {e}）"
            f" ⇒ 请修好或删掉它；**不会**回退到内建表（否则你会以为清单已生效）。") from e
    machines = raw.get("machines") if isinstance(raw, dict) else raw
    if not isinstance(machines, list):
        raise MachineCatalogError(f"外部机台清单缺少 `machines` 列表：{p}")
    errs = validate(machines)
    if errs:
        raise MachineCatalogError(f"外部机台清单不合法：{p} ⇒ " + "；".join(errs[:5]))
    return machines


def validate(machines: list[dict]) -> list[str]:
    """→ 错误说明清单（空＝合格）。判据尽量少而硬：名字必须有、不许重复、类型要对。"""
    errs: list[str] = []
    seen: set[str] = set()
    for i, m in enumerate(machines or []):
        if not isinstance(m, dict):
            errs.append(f"第 {i + 1} 项不是对象")
            continue
        name = str(m.get("name") or "").strip()
        if not name:
            errs.append(f"第 {i + 1} 项缺 `name`")
            continue
        if name in seen:
            errs.append(f"机台名重复：{name}")
        seen.add(name)
        for k in ("equipment_id", "tool_id", "vendor", "model", "max_sample",
                  "location", "serial", "status", "notes"):
            if k in m and not isinstance(m[k], (str, int, float)) and m[k] is not None:
                errs.append(f"{name} 的 `{k}` 类型不对（应为字符串）")
    return errs


def status() -> dict:
    """给 `/api/health` 与排障用（**永不抛**）：清单在不在、几台、错在哪。"""
    p = path()
    try:
        ms = load()
    except MachineCatalogError as e:
        return {"path": str(p), "present": True, "ok": False, "count": 0, "error": str(e)}
    if ms is None:
        return {"path": str(p), "present": False, "ok": True, "count": 0,
                "error": "", "note": "未提供外部清单 ⇒ 使用内建表（原行为）"}
    return {"path": str(p), "present": True, "ok": True, "count": len(ms), "error": ""}
