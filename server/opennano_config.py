"""OpenNano 集中配置:所有本机路径/外部依赖都可用环境变量覆盖(发布版要求)。

.env 或环境变量:
  OPENNANO_WORKSPACE  工作区根(默认:从本文件上溯两级)
  OPENNANO_DATA_ROOT  实验数据目录(默认 <workspace>/个人空间/18_工艺数据资产/03_实验数据)
  OPENNANO_CORE_DIR   core 目录(默认 <DATA_ROOT>/core)
  OPENNANO_DOE_DIR    DOE 执行表目录(可选)
  OPENNANO_KLAYOUT    klayout 可执行文件(默认 /usr/local/bin/klayout)
  OPENNANO_PROJECTS_DIR  画布工程目录(默认 ~/.opennano/projects)
  OPENNANO_DB            知识库 SQLite(默认 ~/.opennano/opennano.db)

⚠️ 最后两项是 2026-09-13 补的：此前它们**写死在 4 个文件里**（main / relayout / repair_edges /
   store），后果是——任何脚本或用例都只能写进**owner正在用的**工程目录和知识库。
   我自己就踩过一次：跑了一趟旧冒烟脚本，它把 `t.json` 存进 ~/.opennano/projects/，
   而界面上「最近修改的工程」正是被载入的那个 ⇒ **正在用的 AR50-T1 画布当场被顶掉**。
   现在：测试/脚本把这些指到临时目录即可，一次也别碰真实数据。
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

#: 画布工程目录（`~/.opennano/projects`）
PROJECTS_DIR = Path(os.environ.get("OPENNANO_PROJECTS_DIR")
                    or (Path.home() / ".opennano" / "projects"))
#: 知识库 SQLite（`~/.opennano/opennano.db`）
DB_PATH = Path(os.environ.get("OPENNANO_DB")
               or (Path.home() / ".opennano" / "opennano.db"))
