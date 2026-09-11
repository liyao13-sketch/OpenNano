"""OpenNano 集中配置:所有本机路径/外部依赖都可用环境变量覆盖(发布版要求)。

.env 或环境变量:
  OPENNANO_WORKSPACE  工作区根(默认:从本文件上溯两级)
  OPENNANO_DATA_ROOT  实验数据目录(默认 <workspace>/个人空间/18_工艺数据资产/03_实验数据)
  OPENNANO_CORE_DIR   core 目录(默认 <DATA_ROOT>/core)
  OPENNANO_DOE_DIR    DOE 执行表目录(可选)
  OPENNANO_KLAYOUT    klayout 可执行文件(默认 /usr/local/bin/klayout)
"""
from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve()                      # …/OpenNano/server/opennano_config.py
WORKSPACE = Path(os.environ.get("OPENNANO_WORKSPACE") or _HERE.parents[2])

DATA_ROOT = Path(os.environ.get("OPENNANO_DATA_ROOT")
                 or (WORKSPACE / "个人空间" / "18_工艺数据资产" / "03_实验数据"))
CORE_DIR = Path(os.environ.get("OPENNANO_CORE_DIR") or (DATA_ROOT / "core"))
DOE_DIR = Path(os.environ.get("OPENNANO_DOE_DIR")
               or (WORKSPACE / "个人空间" / "00_每日任务" / "2026-08-20_DOE实验"))
KLAYOUT = os.environ.get("OPENNANO_KLAYOUT", "/usr/local/bin/klayout")
