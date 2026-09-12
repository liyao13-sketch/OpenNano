"""表单/字段契约的**只读**读取与校验器（工具侧唯一入口，避免自己另编一套枚举）。

权威源（**只读**，工具不写这些文件）：
    - `schema_v0.1.md` §三 受控量名 · §四 method/verification · §十一 eq_state 口径 · §十三 参数键
    - `现象受控词表.csv`（32 词）
    - `ingest/datasets_menu.py` 的 `MFC_PARAM / MENU_PARAM`（参数键映射的唯一真相）

本模块只做两件事：**把权威源读进来给 UI 用** + **提交前校验**（挡住"机台列名当键""0 填成空"这类错误）。
任何枚举都要改 ⇒ 改 schema/词表本身（走【跨线】），不要在 OpenNano 里加常量。
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from .menu_reader import _workspace, parser

SCHEMA_REL = "个人空间/18_工艺数据资产/03_实验数据/schema_v0.1.md"
OBS_REL = "个人空间/18_工艺数据资产/03_实验数据/现象受控词表.csv"

STATUS = ("planned", "running", "done", "aborted")
VERIFICATION = ("已核实", "未核实", "存疑")          # 默认未核实：不许替记录升可信度
METHOD = ("SEM读图", "SEM图上标注", "台阶仪", "椭偏", "应力仪", "设备遥测",
          "计算派生", "样品档案", "记录给出", "口述")
#: 开关量：值为 0 也必须提交（否则分不清"确认没走旁通"和"没记"）
#: `REQUIRED` = 菜单**每步都给**的（实测 gvv1/gvv2 在 19/19 步都在）⇒ 缺键即报错；
#: 其余是**条件性**开关（只在某类步出现）⇒ 缺键只提示、不拦。
SWITCH_REQUIRED = ("gvv1", "gvv2")
SWITCH_CONDITIONAL = ("bias_pulse", "bias_preset", "source_preset",
                      "esc_chuck", "n2_flow", "n2_evac", "pin")
SWITCH_KEYS = SWITCH_REQUIRED + SWITCH_CONDITIONAL


def _schema_path() -> Path:
    env = os.environ.get("OPENNANO_SCHEMA")
    return Path(env) if env else _workspace() / SCHEMA_REL


def _obs_path() -> Path:
    env = os.environ.get("OPENNANO_OBS_VOCAB")
    return Path(env) if env else _workspace() / OBS_REL


def quantities() -> list[str]:
    """§三 受控量名（从 markdown 表格里抽反引号词——不另存一份清单）。"""
    p = _schema_path()
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    seg = text.split("## 三、", 1)[-1].split("\n## ", 1)[0]
    return sorted(set(re.findall(r"`([a-z][a-z0-9_]+)`", seg)))


def observations() -> list[dict]:
    """现象受控词表（32 词）→ 下拉用。"""
    p = _obs_path()
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8-sig") as f:
        return [{"obs_type": r.get("obs_type", ""), "label": r.get("中文名", ""),
                 "category": r.get("类别", ""), "severity": r.get("默认严重度", ""),
                 "hint": r.get("判读提示", ""), "action": r.get("典型对策", "")}
                for r in csv.DictReader(f) if r.get("obs_type")]


class ContractUnavailable(RuntimeError):
    """契约的**真源**拿不到（数据资产没装载 / 菜单解析器缺失）。

    单独一个异常类型，是为了让接口层能给出**可操作的提示**而不是 500：
    "工具侧不另存一份契约副本"是有意为之（否则必然与 schema 漂移），
    代价就是没装载数据资产时表单读不到枚举 —— 这时应当明说，而不是静默给空表。
    """


def param_keys() -> dict:
    """规范参数键（来自 datasets_menu 的映射表：46 列 + MFC 通道 + `_ramp` 后缀）→ 展示用。

    §13.2 规则是"**每条** `… Ramp` 列 → 同名前缀键 + `_ramp`"，所以 `_ramp` 变体必须
    对**所有**映射键都生成（不只是 MFC）。§13.4 另规定时间列合成秒值进
    `steps.duration_s`，但**保留** `process_time_sec` 原秒值键。
    """
    try:
        dm = parser()
        base = {**dm.MFC_PARAM, **dm.MENU_PARAM}
        keep_zero = dm.KEEP_ZERO
    except Exception as e:                            # noqa: BLE001
        raise ContractUnavailable(
            f"菜单解析器（数据线 datasets_menu.py）不可达：{e}；"
            "参数键契约由它定义，工具侧不另存副本") from e
    out: dict[str, dict] = {}
    for col, key in base.items():
        out[key] = {"from": col, "keep_zero": col in keep_zero}
    for col, key in base.items():                     # 每列都有 _ramp 变体
        out.setdefault(key + "_ramp", {"from": col + " Ramp", "keep_zero": False})
    out.setdefault("process_time_sec", {"from": "Process time sec.（§13.4 保留原秒值）",
                                        "keep_zero": False})
    out.setdefault("process_time_sec_ramp", {"from": "Process time sec. Ramp",
                                             "keep_zero": False})
    for extra in ("phase", "machine_step", "step_type", "loop_range", "loop_count"):
        out.setdefault(extra, {"from": "(结构键)", "keep_zero": False})
    return out


def contract() -> dict:
    """一次性给 UI 的全部枚举/键表。"""
    keys = param_keys()
    return {
        "status": list(STATUS),
        "verification": list(VERIFICATION),
        "method": list(METHOD),
        "quantities": quantities(),
        "observations": observations(),
        "param_keys": keys,
        "param_keys_from_machine": [v["from"] for v in keys.values() if v["from"] != "(结构键)"],
        "switch_keys": list(SWITCH_KEYS),
        "switch_required": list(SWITCH_REQUIRED),
        "eq_state": {
            "fields": ["date", "tool", "env_temp_c", "env_rh_pct", "chamber_bg_pa",
                       "chiller_temp_c", "chamber_temp_c", "he_flow", "clean_done", "note"],
            "ranges": {"env_temp_c": (-20, 60), "env_rh_pct": (0, 100)},
            "clean_done": ["是", "否"],
        },
        "rules": [
            "参数键必须用 §十三 规范键（机台列名 RFG1(BIAS) Pulse → bias_pulse）",
            "gvv1/gvv2 等开关量「0 也要提交」（显式写键）",
            "measurements.quantity 取自 §三 受控量名；value 只填数字，没测留空（禁 0/-/N/A）",
            "observations.obs_type 取自现象受控词表（32 词）",
            "verification 默认未核实，不许工具替记录升可信度",
        ],
    }


# ------------------------------------------------------------------ 校验
#: 需要 gvv（旁通阀）开关量的 stage —— 其余工序（PECVD/曝光…）没有这两个阀
GVV_STAGES = ("DRIE", "ICP", "RIE", "RIB", "RIBE")


def check_steps(steps: list[dict], strict: bool = True, stage: str = "") -> list[str]:
    """校验 run 的 steps。返回问题清单（**级别前缀** `[错误]` / `[提示]`）。

    - `strict=True`（**新数据**：表单录入 / 菜单灌参）：键必须 snake_case；DRIE/ICP 类工序
      的 `gvv1/gvv2` 必须在位（0 也要显式提交）。
    - `strict=False`（**历史数据** append-only 回看）：只报结构性非法（含非 snake_case），
      §13.6 的历史遗留键与缺 gvv 只给提示，不当错误。
    - `stage`：给了就**以它为准确认该不该要开关量**（PECVD/曝光没有旁通阀）；
      没给则看步骤里是否出现开关量键来判（保守：只有出现才要求，缺键的工序本来就判不出）。

    ⚠️ 键名表来自数据线 `datasets_menu.py`；**没装载数据资产**时（CI/别人机器）拿不到
    键名 ⇒ 这里是**降级为"只校验不依赖键表的规则"**（非法键名格式、gvv 在位），
    并**明说"键名未校验"** —— 既不 500，也不假装校验过了。
    """
    notes: list[str] = []
    try:
        keys = param_keys()
    except ContractUnavailable as e:
        keys = {}
        notes.append(f"[提示] 键名表不可用 ⇒ **本次未校验键名是否在契约内**（{e}）")
    return notes + _check_steps_with(steps, keys, strict=strict, stage=stage)


def _check_steps_with(steps: list[dict], keys: dict, strict: bool = True,
                      stage: str = "") -> list[str]:
    """`check_steps` 的**纯逻辑内核**（键名表由调用方注入）—— 便于离线单测。"""
    # ⚠️ `need_gvv` 的判定顺序：**先看调用方给的 stage**（工单里说 PECVD 就不该要 gvv）；
    #    stage 没给时才退回"步骤里有没有 gvv 痕迹"（缺键没法判工序，只能保守）。
    #    曾经漏了这一步 ⇒ 传 stage="PECVD" 仍然报"缺 gvv1/gvv2"（2026-09-13 回归网查出）。
    if stage:
        need_gvv = any(s in stage.upper() for s in GVV_STAGES)
    else:
        need_gvv = any(sw in (s.get("param_json") or {}) for s in (steps or [])
                       for sw in SWITCH_KEYS)
    errs: list[str] = []
    for s in steps or []:
        pj = s.get("param_json") or {}
        for k in pj:
            if k in keys:
                continue
            if not re.fullmatch(r"[a-z0-9_]+", k):
                errs.append(f"[错误] 步骤 {s.get('step_order')}: 非法键名 `{k}`（须 snake_case 规范键）")
            elif strict:
                errs.append(f"[提示] 步骤 {s.get('step_order')}: 非契约键 `{k}`"
                            f"（§13.2 兜底允许，但若是常用列请补进契约）")
            else:
                errs.append(f"[提示] 步骤 {s.get('step_order')}: 历史遗留键 `{k}`"
                            f"（§13.6 append-only，新数据不得再新增）")
        if strict and need_gvv:
            for sw in SWITCH_REQUIRED:
                if sw not in pj:
                    errs.append(f"[错误] 步骤 {s.get('step_order')}: 必需开关量 `{sw}` 缺键"
                                f"（0 也必须显式提交）")
    return errs


def errors_only(issues: list[str]) -> list[str]:
    return [i for i in issues if i.startswith("[错误]")]


def check_measurements(rows: list[dict]) -> list[str]:
    """校验 measurements：quantity 受控、value 只数字或空。"""
    qs = set(quantities())
    errs: list[str] = []
    for i, r in enumerate(rows or [], start=1):
        q = (r.get("quantity") or "").strip()
        v = str(r.get("value") if r.get("value") is not None else "").strip()
        if not q:
            errs.append(f"测量 {i}: 缺 quantity")
        elif qs and q not in qs:
            errs.append(f"测量 {i}: quantity `{q}` 不在 §三 受控量名内")
        if v and not re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", v):
            errs.append(f"测量 {i}（{q}）: value `{v}` 不是数字（没测请留空，禁 0/-/N/A）")
        ver = (r.get("verification") or "未核实").strip()
        if ver not in VERIFICATION:
            errs.append(f"测量 {i}: verification `{ver}` 不在 {VERIFICATION}")
    return errs


def check_observations(rows: list[dict]) -> list[str]:
    """校验 observations：obs_type 必须在受控词表。"""
    vocab = {o["obs_type"] for o in observations()}
    errs: list[str] = []
    for i, r in enumerate(rows or [], start=1):
        ot = (r.get("obs_type") or "").strip()
        if not ot:
            errs.append(f"现象 {i}: 缺 obs_type")
        elif vocab and ot not in vocab:
            errs.append(f"现象 {i}: obs_type `{ot}` 不在现象受控词表（32 词）")
    return errs


def check_eq_state(row: dict) -> tuple[dict, list[str]]:
    """校验并归一 eq_state 一行（超量程/认不出的值 ⇒ 留空 + 报警，行保留）。"""
    out, warns = {}, []
    date = (row.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        warns.append(f"date `{date}` 不是 YYYY-MM-DD（该行不入库）")
    else:
        out["date"] = date
    out["tool"] = (row.get("tool") or "(环境)").strip() or "(环境)"
    for f, (lo, hi) in (("env_temp_c", (-20, 60)), ("env_rh_pct", (0, 100))):
        v = str(row.get(f) if row.get(f) is not None else "").strip()
        if not v:
            continue
        try:
            fv = float(v)
        except ValueError:
            warns.append(f"{f} `{v}` 非数字 ⇒ 留空"); continue
        if not (lo <= fv <= hi):
            warns.append(f"{f} {fv} 超量程 [{lo},{hi}] ⇒ 留空"); continue
        out[f] = fv
    cd = (row.get("clean_done") or "").strip().lower()
    if cd in ("是", "y", "yes", "true", "1"):
        out["clean_done"] = "是"
    elif cd in ("否", "n", "no", "false", "0"):
        out["clean_done"] = "否"
    for f in ("chamber_bg_pa", "chiller_temp_c", "chamber_temp_c", "he_flow"):
        if row.get(f) not in (None, ""):
            out[f] = row[f]
    if row.get("note"):
        out["note"] = row["note"]
    return out, warns
