"""设备菜单导出（.grp/.rcp）读取适配器 —— **复用数据线 datasets_menu.py，不重复实现解析器**。

为什么是"适配器"而不是"再写一份解析器"：
    契约铁律要求**只有一处**解析实现（`ingest/datasets_menu.py` 是唯一真相），
    否则两边的参数键/槽位口径必然漂移（工艺侧 2026-09-11 已因此翻过车）。
    本模块只做三件事：①按需加载那份解析器 ②把它的输出翻成 OpenNano 的 run steps
    ③加一层"配对可靠性 / 槽位地盘 / 三段组合"的护栏。

设备惯例（2026-09-08 口径 · RIE-400iPB）：
    一次完整上机 = chuck(recipe 2) → 刻蚀(recipe 与 group 同号) → de-chuck(recipe 4)
    即 `group N = [2, N, 4]`；group 槽文件（.rcp）里那条 20 值序列是**引用序列**，
    但 `[2,N,4]` 是设备实际执行的三段，灌参以三段为准。

槽位地盘（硬规则，owner定）：
    G01–G10 设备维护 · G11–G30 蓝本(不使用) · G31–G38 调试计划 · G39–G49 备份
    · **G50+ 他人菜单 ⇒ 不保存/不分析/不入库/不归档**（SCOPE_MAX=49）。

⚠️ 两份文件必须**同刻导出**：不同刻会出现"名字 ↔ 槽位"漂移（本次实测差 18 分钟即漂移）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

#: 唯一真相：数据线解析器（可用 OPENNANO_MENU_PARSER 覆盖；默认按工作区推断）
_DEFAULT_REL = "个人空间/18_工艺数据资产/03_实验数据/ingest/datasets_menu.py"
_DEFAULT_MENU_REL = "个人空间/18_工艺数据资产/06_设备菜单"

#: 三段组合：chuck / etch(=group 同号) / de-chuck
SEG_CHUCK, SEG_DECHUCK = 2, 4
SEG_LABEL = {SEG_CHUCK: "chuck", SEG_DECHUCK: "dechuck"}
SCOPE_MAX = 49
#: 同刻导出容许差（分钟）：超过即提示"配对不可靠"
PAIR_TOL_MIN = 5


class MenuParserUnavailable(RuntimeError):
    """找不到/无法加载共享解析器时抛出（提示怎么修，不静默降级）。"""


def _workspace() -> Path:
    env = os.environ.get("OPENNANO_WORKSPACE")
    if env:
        return Path(env)
    # OpenNano/server/kb/menu_reader.py → 上溯到工作区根（含"个人空间"）
    for p in Path(__file__).resolve().parents:
        if (p / "个人空间").is_dir():
            return p
    return Path(__file__).resolve().parents[3]


def _parser_path() -> Path:
    env = os.environ.get("OPENNANO_MENU_PARSER")
    return Path(env) if env else _workspace() / _DEFAULT_REL


def default_menu_dir() -> Path:
    env = os.environ.get("OPENNANO_MENU_DIR")
    return Path(env) if env else _workspace() / _DEFAULT_MENU_REL


_PARSER = None


def parser():
    """加载共享解析器模块（单例）。失败给出可操作提示，绝不静默返回空。

    ⚠️ datasets_menu.py 顶部 `from core_schema import FIELDS` ⇒ 加载时必须把
    它所在目录放进 `sys.path`（加载完即还原，不污染宿主进程的导入路径）。
    """
    global _PARSER
    if _PARSER is not None:
        return _PARSER
    p = _parser_path()
    if not p.exists():
        raise MenuParserUnavailable(
            f"找不到共享菜单解析器：{p}\n"
            f"→ 用 OPENNANO_MENU_PARSER=<datasets_menu.py 路径> 指定，"
            f"或确认工作区（OPENNANO_WORKSPACE）指向含「个人空间/」的根目录。")
    spec = importlib.util.spec_from_file_location("opennano_datasets_menu", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    backup = list(sys.path)
    sys.path.insert(0, str(p.parent))
    try:
        spec.loader.exec_module(mod)      # type: ignore[union-attr]
    except Exception as e:                # noqa: BLE001
        raise MenuParserUnavailable(f"加载共享解析器失败（{p}）：{type(e).__name__}: {e}") from e
    finally:
        sys.path[:] = backup
    _PARSER = mod
    return mod


# ------------------------------------------------------------------ 工具
def _stamp_minutes(path: Path) -> float | None:
    """文件名里的 `YYYYMMDDhhmmss` → 分钟级时间戳（用于配对可靠性判断）。"""
    m = re.search(r"(20\d{6})(\d{6})", path.name)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return dt.timestamp() / 60.0


def _stamp_text(path: Path) -> str:
    m = re.search(r"(20\d{6})(\d{6})", path.name)
    return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}" if m else ""


def slot_zones() -> list[dict]:
    return [
        {"range": "G01–G10", "use": "设备维护", "keep": True},
        {"range": "G11–G30", "use": "蓝本（不使用）", "keep": True},
        {"range": "G31–G38", "use": "调试计划", "keep": True},
        {"range": "G39–G49", "use": "备份", "keep": True},
        {"range": "G50+", "use": "他人菜单 ⇒ 不保存/不分析/不入库/不归档", "keep": False},
    ]


# ------------------------------------------------------------------ 解析
def load_menu(export_dir: str | Path) -> dict:
    """解析一个「{YYYYMMDD}_菜单导出/」目录 → 预览用结构（不写任何文件）。"""
    dm = parser()
    root = Path(export_dir).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"不是目录：{root}")
    grps = sorted(root.rglob("*.grp"))
    rcps = sorted(root.rglob("*.rcp"))
    if not grps and not rcps:
        raise FileNotFoundError(f"目录里没有 .grp/.rcp：{root}")

    def _rows(paths):
        out, skipped = [], 0
        for p in paths:
            got = dm.parse_grp(p) if p.suffix.lower() == ".grp" else dm.parse_rcp(p)
            for r in got:
                slot = int(r["recipe_id"][-3:])
                if slot > SCOPE_MAX:          # G50+ 一律不入
                    skipped += 1
                    continue
                out.append({"slot": slot, "recipe_id": r["recipe_id"],
                            "name": (r.get("name") or "").strip(),
                            "file": p.name, "zones": dm.zone_of(slot)})
        return out, skipped

    g_rows, g_skip = _rows(grps) if grps else ([], 0)
    r_rows, r_skip = _rows(rcps) if rcps else ([], 0)

    # 配对可靠性：两份文件各自的时间戳差
    g_min = min((_stamp_minutes(p) for p in grps if _stamp_minutes(p)), default=None)
    r_min = min((_stamp_minutes(p) for p in rcps if _stamp_minutes(p)), default=None)
    delta = abs(g_min - r_min) if (g_min and r_min) else None
    warning = None
    if delta is not None and delta > PAIR_TOL_MIN:
        warning = (f"两份文件不同刻导出（相差 {delta:.0f} 分钟 > {PAIR_TOL_MIN} 分钟）"
                   f"⇒ 名字↔槽位配对**不可靠**，请重新导出后使用")

    return {
        "dir": str(root),
        "grp_files": [{"name": p.name, "stamp": _stamp_text(p)} for p in grps],
        "rcp_files": [{"name": p.name, "stamp": _stamp_text(p)} for p in rcps],
        "pair_delta_min": delta,
        "pair_warning": warning,
        "zones": slot_zones(),
        "scope_max": SCOPE_MAX,
        "recipes": g_rows,                 # 来自 .grp（recipe 库）
        "groups": r_rows,                  # 来自 .rcp（group 库）
        "skipped_out_of_scope": g_skip + r_skip,
    }


def recipe_slot(slot: int, export_dir: str | Path) -> dict:
    """取某 recipe 槽（已解析结构；步号 = 机台槽位号）。"""
    dm = parser()
    if int(slot) > SCOPE_MAX:
        raise ValueError(f"槽 {slot} 属他人菜单（>{SCOPE_MAX}）⇒ 不分析")
    return dm.recipe_by_slot(int(slot), root=Path(export_dir).expanduser())


def segment_steps(slot: int, export_dir: str | Path, phase: str | None = None) -> list[dict]:
    """单个 recipe 槽 → 实际执行的步列表（含 role / duration / 规范键参数）。"""
    dm = parser()
    rec = recipe_slot(slot, export_dir)
    phase = phase or SEG_LABEL.get(int(slot)) or "etch"
    out = []
    for st in dm.executed_steps(rec):
        params = dm.map_params(st.get("params") or {})
        params["machine_step"] = st.get("machine_step", st.get("i"))
        if phase:
            params["phase"] = phase
        out.append({
            "machine_step": st.get("machine_step", st.get("i")),
            "step_type": st.get("type", ""),
            "role": dm.role_from_params(st.get("params") or {}),
            "duration_s": dm.menu_duration(st.get("params") or {}),
            "params": params,
        })
    return out


def group_steps(group_slot: int, export_dir: str | Path) -> dict:
    """「用 group N 灌参」的核心：group N → 三段 [2, N, 4] → 一个 run 的 steps。

    返回 {"segments":[…], "steps":[{step_order,step_name,role,duration_s,param_json}…],
          "counts":{segment:n}, "skipped_slots":{slot:[…]}, "phase_note":…}
    步号口径：`step_order` **全局递增 1..N**（duration/steps 表列需要单调），
    机台槽位号保留在 `param_json.machine_step`（工艺侧"步号=机台槽位号"的口径不丢）。
    """
    dm = parser()
    segs = [(SEG_CHUCK, "chuck"), (int(group_slot), "etch"), (SEG_DECHUCK, "dechuck")]
    steps, counts, skipped = [], {}, {}
    for slot, phase in segs:
        got = segment_steps(slot, export_dir, phase)
        counts[phase] = {"slot": slot, "executed": len(got),
                         "defined": len(recipe_slot(slot, export_dir)["steps"]),
                         "name": recipe_slot(slot, export_dir)["name"]}
        sk = dm.skipped_slots(recipe_slot(slot, export_dir))
        if sk:
            skipped[phase] = {"slot": slot, "skipped_machine_slots": sk}
        for g in got:
            steps.append({
                "step_order": len(steps) + 1,
                "machine_step": g["machine_step"],
                "step_name": f"{phase}-{g['machine_step']:02d}",
                "role": g["role"],
                "duration_s": g["duration_s"],
                "param_json": g["params"],
            })
    return {
        "group": int(group_slot),
        "group_seq": [SEG_CHUCK, int(group_slot), SEG_DECHUCK],
        "segments": counts,
        "steps": steps,
        "total_steps": len(steps),
        "skipped_slots": skipped,
        "defined_total": sum(v["defined"] for v in counts.values()),
        "note": ("steps 只含**实际执行**步；配方定义仍保 30 槽（未启用 loop 区间的槽不进 run）"),
    }


def recipe_roles(group_slot: int, export_dir: str | Path) -> list[str]:
    """该 group 的 role 序列（给 UI 展示，不调顺 Bosch 顺序）。"""
    return [s["role"] for s in group_steps(group_slot, export_dir)["steps"]]
