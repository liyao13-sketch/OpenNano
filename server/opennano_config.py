"""OpenNano 集中配置:所有本机路径/外部依赖都可用环境变量覆盖(发布版要求)。

.env 或环境变量:
  OPENNANO_WORKSPACE  工作区根(默认:从本文件上溯两级)
  OPENNANO_DATA_ROOT  实验数据目录(默认 <workspace>/个人空间/32_工艺数据资产/03_实验数据)
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
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()                      # …/OpenNano/server/opennano_config.py
WORKSPACE = Path(os.environ.get("OPENNANO_WORKSPACE") or _HERE.parents[2])

#: 工作区里的个人空间根 —— **只在这一处写它的名字**，其余一律引用本常量
#: （公开层棘轮会数这个目录名：写一次是 1，散着写就是 N；改名时也只改这一行）
PERSONAL = WORKSPACE / "个人空间"

def _doe_dir() -> Path:
    """DOE 执行表目录 —— **单一权威位**：`32_工艺数据资产/07_执行表/`。

    2026-10-01（工单 `20261001-工艺线-to-工具线-01`）：**下线 33 侧旧位兜底**。
    旧位 `33_工艺资料/干法刻蚀/数据科学/DOE设计/执行表_2026-08-20` 只是一条**待拆的过渡软链**
    （`数据科学/` 已解散）—— 静默回退到它 ＝ "看着有兜底，其实指向一个马上消失的路径"，
    而它的失败形态恰恰最坏：**扫表得 0 张、不报错**（2026-09-12 那次 E1 就是同款）。

    现在的口径：
      · 新位在 ⇒ 用它（正常，静默）；
      · 新位不在、**但工作区在本机** ⇒ **强告警**（`stderr`，不静默）并仍返回新位 ——
        让下游如实报"扫到 0 张表"，而不是假装正常；
      · 工作区不在这台机器（CI / 公网 clone）⇒ 不吵（那里本来就没有数据资产）。
    """
    new = PERSONAL / "32_工艺数据资产" / "07_执行表"
    if new.is_dir():
        return new
    if PERSONAL.is_dir():
        print(f"[DOE] ⚠️ 执行表目录不存在：{new}\n"
              f"       ⇒ 扫表会得到 **0 张表**（不是「没有表」，是「路径不对」）。\n"
              f"       修法：确认 `32_工艺数据资产/07_执行表/` 在位，或用 `OPENNANO_DOE_DIR` 显式指定。",
              file=sys.stderr)
    return new


def _data_root() -> Path:
    """实验数据根 —— **双路兼容**（2026-09-30 owner 裁定：`03_实验数据` → **`03_数据核心`**，名字更准）。

    新名优先、旧名兜底 ⇒ **改名前后工具都能跑**（物理改名属体系重整/数据线，不属工具线写边界）。
    ⚠️ 注意：跨线指针（`kb/pointer_check.py` 14 条）**故意保持字面**、不做双路 ——
    它是判据，改名时**应该红**，红了才是"记得同步"；这里兜底只为不让服务在过渡期瘫掉。
    """
    new = PERSONAL / "32_工艺数据资产" / "03_数据核心"
    old = PERSONAL / "32_工艺数据资产" / "03_实验数据"
    if new.is_dir():
        return new
    return old


DATA_ROOT = Path(os.environ.get("OPENNANO_DATA_ROOT") or _data_root())
CORE_DIR = Path(os.environ.get("OPENNANO_CORE_DIR") or (DATA_ROOT / "core"))
DOE_DIR = Path(os.environ.get("OPENNANO_DOE_DIR") or _doe_dir())
# ⚠️ DOE 执行表搬过两次家（2026-09-12 从 00_每日任务 → 33_；2026-09-30 裁：33_ → 32_/07_执行表）：
#    第一次只改代码没留兜底 ⇒ 旧默认指向已删目录 ⇒ **扫表静默为空**（E1）。
#    现在 `_doe_dir()` 新位优先＋旧位兜底；再搬家时改那一个函数即可，或用 env 覆盖。
KLAYOUT = os.environ.get("OPENNANO_KLAYOUT", "/usr/local/bin/klayout")

#: 画布工程目录（`~/.opennano/projects`）
PROJECTS_DIR = Path(os.environ.get("OPENNANO_PROJECTS_DIR")
                    or (Path.home() / ".opennano" / "projects"))
#: 知识库 SQLite（`~/.opennano/opennano.db`）
DB_PATH = Path(os.environ.get("OPENNANO_DB")
               or (Path.home() / ".opennano" / "opennano.db"))

# ---- 团队化（P0 · 2026-09-15）：账号 / 会话 / 留痕 ------------------------------------
# ⚠️ 与上面两项同一条教训：**不许写死**。测试与脚本一律指到临时目录，
#    否则"跑一趟用例"就能把真实账号库/留痕顶掉（工程目录那次是真踩过的）。
#: 账号库（`~/.opennano/users.json`，0600）
ACCOUNTS_PATH = Path(os.environ.get("OPENNANO_ACCOUNTS")
                     or (Path.home() / ".opennano" / "users.json"))
#: 服务端签名密钥（`~/.opennano/.server_secret`，0600；会话令牌用它签）
SERVER_SECRET = Path(os.environ.get("OPENNANO_SERVER_SECRET")
                     or (Path.home() / ".opennano" / ".server_secret"))
#: 操作留痕（append-only JSONL）
AUDIT_LOG = Path(os.environ.get("OPENNANO_AUDIT")
                 or (Path.home() / ".opennano" / "audit.log"))

# ---- 资产库 / 版图输出（2026-09-16 审计补：此前这三处写死在各自模块里）----------------
# ⚠️ 与上面同一条教训（"不许写死"）：写死 ⇒ 测试与部署都改不动，跑趟用例就可能顶掉真实资产。
#: 设备/参数/影响规则资产库（`~/.opennano/library.json`）
LIBRARY_PATH = Path(os.environ.get("OPENNANO_LIBRARY")
                    or (Path.home() / ".opennano" / "library.json"))
#: GDS 输出目录（`~/.opennano/gds`）
GDS_DIR = Path(os.environ.get("OPENNANO_GDS_DIR")
               or (Path.home() / ".opennano" / "gds"))
