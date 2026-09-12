"""回归网共享装置（pytest）。

三条纪律（跟产品同一条零号铁律）：
  1. **不写数据资产** —— core 只读；所有合成数据落在 `tmp_path`；
  2. **不依赖网络**；
  3. **不靠记忆** —— `OPENNANO_WORKSPACE` 显式指向本仓所在工作区，绝不靠"上溯找 `个人空间`"碰运气。

同时提供 `reset_caches`（自动）——把 kb 里那几个"进程内缓存"清干净，
原因：它们是**单例**，同一进程内跑多个用例时，第二个用例会读到第一个的 core
（这正是"语义标注常只在 core 侧"踩过的坑的同族问题）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]          # …/OpenNano/server （本仓根 = REPO.parent）
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))                   # 让 `import opennano_config` / `import main` 可用


def _find_workspace() -> Path:
    """工作区根 = **含有 `个人空间/` 的那一级**。

    ⚠️ 别用"仓库根的上一级"这种猜测：本机 `OpenNano/` 是**仓库根**，
    而工作区根是它的上一层（`个人空间/` 与 `OpenNano/` 平级）。
    猜错的后果不是报错，而是**用例被静默跳过（假绿）** —— 2026-09-13 实际踩到。
    """
    env = os.environ.get("OPENNANO_WORKSPACE")
    if env and (Path(env) / "个人空间").is_dir():
        return Path(env)
    for p in (REPO.parent, *REPO.parents):
        if (p / "个人空间").is_dir():
            return p
    return REPO.parent                              # 合成环境下没有真源，退回仓库根


WS_ROOT = _find_workspace()
_DATA_ROOT = WS_ROOT / "个人空间/18_工艺数据资产/03_实验数据"
_REAL_CORE_DIR = _DATA_ROOT / "core"                # 真 core（只读用例用；评测/CI 上不存在）
# ⚠️ **必须显式赋值，不能 setdefault**：环境/shell 里若残留旧值，用例会静默回退到
#    "core 里没有 ⇒ 用习惯序"的分支 ⇒ 看着通过，其实没测到 core 回读（2026-09-13 实际踩到）。
os.environ["OPENNANO_WORKSPACE"] = str(WS_ROOT)
os.environ["OPENNANO_CORE_DIR"] = str(_REAL_CORE_DIR)


def seed_core(core_dir: Path, runs=None, batches=None, samples=None,
              steps=None, measurements=None, observations=None) -> Path:
    """在 `core_dir` 里写出一套**合成 core**（列名照真 core），返回该目录。

    只写调用方给的 `tmp_path`；列顺序照 core CSV 真表头。
    """
    import csv
    core_dir = Path(core_dir)
    core_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "runs": (["run_id", "batch_id", "sample_id", "stage", "stage_seq", "date",
                  "t_start", "t_end", "tool", "tool_id", "recipe_id", "operator",
                  "purpose", "parent_run_id", "env_temp_c", "env_rh_pct", "status",
                  "note", "run_nature"], runs or []),
        "batches": (["batch_id", "series", "title", "owner", "purpose", "wafer_size",
                     "substrate_json", "planned_stages", "started_on", "status", "note",
                     "sample_spec_json"], batches or []),
        "samples": (["sample_id", "batch_id", "position", "role", "status", "mask_batch",
                     "note", "parent_sample_id"], samples or []),
        "steps": (["step_id", "run_id", "step_order", "machine_step", "step_name", "role",
                   "duration_s", "pressure", "pressure_unit", "param_json", "note"],
                  steps or []),
        "measurements": (["meas_id", "run_id", "sample_id", "quantity", "value", "unit",
                          "method", "loc", "n", "uncertainty", "source_artifact_id",
                          "measured_by", "verification", "note"], measurements or []),
        "observations": (["obs_id", "run_id", "sample_id", "obs_type", "severity",
                          "description", "judgement", "action", "artifact_id",
                          "recorded_by", "date"], observations or []),
    }
    for name, (header, rows) in tables.items():
        p = core_dir / f"{name}.csv"
        if not rows and p.exists():
            continue                                # 没给内容就别清掉已有夹具
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in header})
    return core_dir


#: 判空方式是 `key in cache`（不是 `is not None`）的 dict 缓存 —— 只能就地清空，不能置 None
_DICT_CACHES = {"_GRP_SLOTS_CACHE"}


@pytest.fixture(autouse=True)
def _isolate_kb_state(tmp_path, monkeypatch):
    """每个用例：KB 缓存清空 + core 指向（默认）本用例的私有空目录。

    需要 core 的用例自己 `monkeypatch.setattr(ap, "CORE_DIR", …)` 或改 `OPENNANO_CORE_DIR`。
    """
    from kb import append_pack as ap
    from kb import batch_runs as br
    from kb import menu_reader as mr
    from kb import batch_events as be
    from kb import lit_ingest as li

    empty_core = tmp_path / "core"
    empty_core.mkdir(exist_ok=True)
    monkeypatch.setattr(ap, "CORE_DIR", empty_core)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(empty_core))
    # 事件台账默认指向内存里不存在的路径 ⇒ 不读真台账
    monkeypatch.setenv("OPENNANO_BATCH_EVENTS", str(tmp_path / "batch_events.csv"))
    # 不扫真工作区的实验包（否则夹具会被真实 parent 污染：既假绿也拖慢）
    monkeypatch.setenv("OPENNANO_PACKS_ROOT", "")

    def _reset():
        for mod in (br, ap, be, li, mr):
            for name in dir(mod):
                if not name.endswith("_CACHE"):
                    continue
                val = getattr(mod, name)
                if isinstance(val, dict):
                    # 判空方式是 `is not None` 的缓存 → 归 None；判 `in` 的 → 就地清空
                    if name in _DICT_CACHES:
                        val.clear()
                    else:
                        setattr(mod, name, None)
        mr._PARSER = None

    _reset()
    yield
    _reset()


# ------------------------------------------------------------------ 工作区真源（只读）
@pytest.fixture
def ws_root() -> Path:
    return Path(os.environ["OPENNANO_WORKSPACE"])


@pytest.fixture
def core_dir(ws_root):
    """真 core 目录（**只读**用例用；不存在就跳过）。"""
    p = ws_root / "个人空间/18_工艺数据资产/03_实验数据/core"
    if not p.is_dir():
        pytest.skip(f"真 core 不在本机：{p}（CI 上这类用例自动跳过）")
    return p


#: 合成 schema 的最小内容 —— 只含**契约解析真正读的**三段（§三/§四/§十一）。
#: 量名词取自真 schema（照抄，不自己发明）；这是"别人机器没装数据资产"时的替身，
#: **不改真源、不新增第四套库**（真源在时一律用真源，见 test_form_contract 的对比用例）。
SYNTH_SCHEMA = """# 合成 schema（回归网夹具 · 非真源）
## 三、`quantity` 受控量名
`film_thickness_nm` `nu_pct` `refractive_index` `stress_mpa` `cd_top_nm` `cd_bottom_nm`
`cd_mid_nm` `cd_delta_nm` `pitch_nm` `cd_loss_nm` `final_cd_nm` `mask_cd_nm` `depth_nm`
`depth_center_nm` `depth_top_nm` `depth_bottom_nm` `er_nm_min` `selectivity` `overetch_nm`
`loop_count` `swa_deg` `bow_nm` `scallop_nm` `scallop_pitch_nm` `roughness_nm` `ler_nm`
`lwr_nm` `undercut_nm` `footing_nm` `trench_top_nm` `trench_bottom_nm` `mask_thickness_nm`
`mask_remaining_nm` `mask_consumed_nm` `source_w` `bias_w` `pressure_actual`
`chamber_bg_pa` `env_temp_c` `env_rh_pct`

## 四、`method` 与 `verification`（可信度必须显式）
- `method`：`SEM读图` · `SEM图上标注` · `台阶仪` · `椭偏` · `应力仪` · `设备遥测` · `计算派生` · `样品档案` · `记录给出` · `口述`
- `verification`：`已核实` · `未核实` · `存疑`

## 十一、eq_state 记录口径
| 字段 | 单位 | 量程 |
|---|---|---|
| `env_temp_c` | ℃ | -20 ~ 60 |
| `env_rh_pct` | % | 0 ~ 100 |
"""

#: 合成现象受控词表（真源 32 词；这里给 3 条用于逻辑断言）
SYNTH_OBS_VOCAB = """obs_type,中文名,类别,默认严重度,判读提示,典型对策,出处示例
OBS-RESIDUE,残胶/未显影净,图形/光刻,major,底部残留,加显影时间,示例
OBS-GRASS,黑硅/草状,刻蚀,major,表面草状,查 O2/钝化比,示例
OBS-UNDERCUT,侧掏,刻蚀,minor,侧壁掏空,调 bias,示例
"""


@pytest.fixture
def contract_source(tmp_path, ws_root, monkeypatch):
    """契约真源：**真源优先，没有才用合成夹具**。

    返回 `"real"` / `"synth"` —— 用例据此决定断言强度（真源上可以断言 32 词、51 个量名词，
    合成夹具上只断言"逻辑跑通"）。**任何情况下都不写数据资产。**
    """
    if (ws_root / "个人空间/18_工艺数据资产/03_实验数据/schema_v0.1.md").exists():
        return "real"
    s = tmp_path / "schema_v0.1.md"
    v = tmp_path / "现象受控词表.csv"
    s.write_text(SYNTH_SCHEMA, encoding="utf-8")
    v.write_text(SYNTH_OBS_VOCAB, encoding="utf-8")
    monkeypatch.setenv("OPENNANO_SCHEMA", str(s))
    monkeypatch.setenv("OPENNANO_OBS_VOCAB", str(v))
    return "synth"


@pytest.fixture
def menu_export(ws_root):
    """真菜单导出目录（若本机没有就跳过）。"""
    base = ws_root / "个人空间/18_工艺数据资产/06_设备菜单"
    if not base.is_dir():
        pytest.skip(f"本机没有设备菜单目录：{base}")
    dumps = sorted(p for p in base.rglob("*_菜单导出") if p.is_dir())
    if not dumps:
        pytest.skip(f"{base} 下没有 `*_菜单导出` 目录")
    return dumps[-1]                                # 取最近一次导出
