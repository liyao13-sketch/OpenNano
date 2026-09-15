"""操作留痕（append-only JSONL）：**谁 / 何时 / 做了什么 / 对什么 / 成没成**。

## 为什么是独立文件，而不是写进 core

写边界：**工具不写 core**（core 是数据线的地，权威在 CSV + `build_core`）。
所以工具侧的人事留痕落在自己的日志里；将来若 core 要记"谁录的"，那是【跨线】契约变更，
走数据线的表（`runs.operator` / `measurements.measured_by` / `observations.recorded_by` 已有这些列）。

## 三条口径

1. **append-only**：只追加，不改写、不删除（团队记忆的"事后说得清"全靠它）。
2. **记事实，不记正文**：只写人/动作/对象/结果与**短摘要**，不写请求正文（正文可能含路径、
   客户样品信息、大块参数）—— 要证据请指向包/文件/证据图。
3. **不阻塞业务**：留痕失败不能把用户的保存/导出搞挂（写不进去就记到 stderr 并放行）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import opennano_config as cfg

_LOCK = threading.Lock()


def _path() -> Path:
    """⚠️ **每次现读 env**（`opennano_config` 的常量是导入时冻结的 ⇒ 测试改 env 不起作用，
    曾因此把留痕写进真 `~/.opennano/audit.log`；配置在用时读才叫可覆盖）。"""
    return Path(os.environ.get("OPENNANO_AUDIT") or cfg.AUDIT_LOG)


def record(actor: str, action: str, target: str = "", detail: str = "",
           ok: bool = True) -> None:
    """追加一条。`actor` = 登录用户名（未登录写 `anonymous`）。"""
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": actor or "anonymous",
           "action": action, "target": target, "detail": (detail or "")[:300],
           "ok": bool(ok)}
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _LOCK:
            # O_APPEND + 单次 write：多线程/多进程追加不会互相截断（行 < 4KB）
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    except Exception as e:  # noqa: BLE001 —— 留痕失败不许把业务搞挂
        print(f"[audit] 写不进去（{type(e).__name__}: {e}）：{row}", file=sys.stderr)


def tail(limit: int = 200, actor: str = "") -> list[dict]:
    """最近 `limit` 条（倒序＝最新在前）。文件不存在 → 空表（不是错误）。"""
    p = _path()
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue                    # 半行/坏行跳过，不让一行脏数据挡住整个台账
                if actor and d.get("actor") != actor:
                    continue
                out.append(d)
    except OSError:
        return []
    return list(reversed(out[-max(1, int(limit)):]))
