"""GDS 生成包装:调 klayout 无头运行 gds_gen.py。"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

_GEN = Path(__file__).parent / "gds_gen.py"
from opennano_config import KLAYOUT as _KLAYOUT  # 可在 .env 覆盖


def generate_gds(config: dict, out_dir: str | Path | None = None) -> dict:
    from opennano_config import GDS_DIR
    out_dir = Path(out_dir) if out_dir else GDS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"layout_{uuid.uuid4().hex[:8]}.gds"
    cfg = {**config, "output": str(out)}
    cfg_path = out_dir / f".cfg_{uuid.uuid4().hex[:8]}.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    env = {**os.environ, "GDS_CONFIG": str(cfg_path)}
    try:
        r = subprocess.run([_KLAYOUT, "-b", "-r", str(_GEN)], env=env,
                           capture_output=True, text=True, timeout=90)
    finally:
        cfg_path.unlink(missing_ok=True)
    if not out.exists():
        raise RuntimeError("GDS 生成失败: " + (r.stderr or r.stdout))
    return {"path": str(out), "size": out.stat().st_size,
            "log": (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""}
