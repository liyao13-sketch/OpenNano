"""klink 实时绘制包装:用系统 python(带 klink)跑 klink_gen.py。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_GEN = Path(__file__).parent / "klink_gen.py"
_PYTHON = "/opt/anaconda3/bin/python3"


def live_draw(config: dict) -> dict:
    cfg_path = Path.home() / ".opennano" / ".klink_cfg.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    env = {**os.environ, "KLINK_CONFIG": str(cfg_path)}
    try:
        r = subprocess.run([_PYTHON, str(_GEN)], env=env,
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return {"ok": False, "error": f"找不到 python: {_PYTHON}"}
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    msg = tail[-1] if tail else ""
    if r.returncode != 0:
        return {"ok": False, "error": msg or "klink 连接失败(KLayout GUI 未运行?)"}
    return {"ok": True, "log": msg}
