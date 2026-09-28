"""core 契约里「机台口径」的**加载器**（工具侧）—— 导出/消费都走这里。

## 这是什么（2026-09-26 · 工单 `20260915-助手线-to-兼-01` B2-残C 的 C3）

**旧形态是「字面量镜像」**：13 个真机台 `tool_id` 与厂名型号直接写在代码里 ——
公开仓库因此带着真实实验室指纹（修一次要动代码、发版）。

**新形态是「加载器」**：口径表从**数据**里读，代码里**零真机台/厂名**。
数据线 2026-09-17 已裁 **(a)**：**`core_schema.TOOL_DISPLAY` 仍是唯一真相**，
工具侧只做「**导出 → 加载 → 比对**」（派生物 `<core>/machine_tool_display.json`，
`schema_v0.1.md §十九`）。

## 加载顺序（C3 原文，逐级回退）

| 序 | 来源 | 说明 |
|---|---|---|
| ① | `OPENNANO_TOOL_DISPLAY` 指向的文件 | 显式指定（部署/排障用）；**指了就必须能用**，坏 ⇒ 抛错 |
| ② | 工作区 core 位置 `<CORE_DIR>/machine_tool_display.json` | 真清单（数据线派生物 · `mode: enforced`）|
| ③ | 同目录内置 `tool_display.demo.json` | **中性样例**（零真机台）⇒ 公开 clone / CI 用 |

**失败一律出声**（沿用「落哨兵 + 告警」纪律）：显式路径坏 / 真清单存在但坏 ⇒ **抛 `ToolDisplayError`**
（不静默降级 —— 否则运维会以为真清单生效了）；只有「**真清单不存在**」才是正常回退到 demo。
demo 缺了/坏了是仓库事故 ⇒ 也抛。

⚠️ **`mode: demo` 不是权威**：跨线判据（G2）见 `degraded`/`mode != enforced` 时必须报**「跳过」**，
不许报通过。

## 消费者（改名会连带）
`resolve_tool`（导出侧唯一入口）· `machine_drift`（口径漂移判据）· `machine_defaults`（实测默认值）
· `tests/test_tool_identity.py`（G2）· `main.py` 的 `/api/health`。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

#: 「机台未记录 / 尚未定」的唯一哨兵 —— **语义常量**（不是机台身份，故留在代码里；
#: 与数据线 `core_schema.TOOL_ID_SENTINEL` 逐字一致，由 G2 判据守）
TOOL_ID_SENTINEL = "UNKNOWN"

#: 哨兵对应的唯一显示名（语义常量，同上）
TOOL_UNKNOWN_DISPLAY = "UNKNOWN（机台未记录）"

#: 仓库内置的中性样例清单（**零真机台**；公开 clone / CI 用）
DEMO_PATH = Path(__file__).resolve().parent / "tool_display.demo.json"

#: 与本模块同期的清单格式版本（数据线 `ingest/export_tool_display.py::SCHEMA_VERSION`）
SCHEMA_VERSION = "machine-display.v1"


class ToolDisplayError(RuntimeError):
    """清单**指了/存在但读不动** —— 出声，绝不静默退回样例（那会让真机台静默变成"未登记"）。"""


#: 加载状态（给 `/api/health`、判据与排障用；**永不抛**）
_LOAD: dict = {"source": "", "path": "", "mode": "", "count": 0, "error": "", "degraded": False}


# ---------------------------------------------------------------- 读取与校验

def entry_digest(tool: list[dict], sentinel: str, sentinel_display: str) -> str:
    """**只对条目**算摘要（与数据线 `export_tool_display.py::entry_digest` 逐字同算法）。

    两边一致才有意义：本加载器因此能校验**他们生成的** `machine_tool_display.json`
    （头部哈希与条目对不上 ⇒ 清单被手改过 / 生成器变了）。
    """
    canon = json.dumps(
        {"tool": [[str(t.get("tool_id") or ""), str(t.get("display") or "")] for t in tool],
         "sentinel": sentinel, "sentinel_display": sentinel_display},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def validate(obj) -> list[str]:
    """→ 错误清单（空＝合格）。判据少而硬：结构对、条目非空、`tool_id` 不重复。"""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["清单不是 JSON 对象"]
    tool = obj.get("tool")
    if not isinstance(tool, list) or not tool:
        return ["清单缺 `tool` 列表（或为空）"]
    seen: set[str] = set()
    for i, t in enumerate(tool):
        if not isinstance(t, dict):
            errs.append(f"第 {i + 1} 条不是对象")
            continue
        tid = str(t.get("tool_id") or "").strip()
        disp = str(t.get("display") or "").strip()
        if not tid:
            errs.append(f"第 {i + 1} 条缺 `tool_id`")
        elif tid in seen:
            errs.append(f"`tool_id` 重复：{tid}")
        else:
            seen.add(tid)
        if not disp:
            errs.append(f"第 {i + 1} 条（{tid or '?'}）缺 `display`")
    return errs


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ToolDisplayError(f"机台清单不存在：{path}") from e
    except (ValueError, OSError) as e:
        raise ToolDisplayError(
            f"机台清单读不动/不是合法 JSON：{path}（{type(e).__name__}: {e}）"
            f" ⇒ 请修好或删掉它；**不会**静默退回中性样例。") from e


def parse(obj, path: Path | str = "") -> tuple[dict[str, str], str]:
    """校验后的清单 → `(有序映射, mode)`。**顺序保留**（G2 会把顺序算进去）。"""
    errs = validate(obj)
    if errs:
        raise ToolDisplayError(f"机台清单不合法：{path} ⇒ " + "；".join(errs[:5]))
    tool = obj["tool"]
    sentinel = str(obj.get("sentinel") or TOOL_ID_SENTINEL).strip()
    sentinel_disp = str(obj.get("sentinel_display") or TOOL_UNKNOWN_DISPLAY).strip()
    declared = obj.get("sha256")
    if declared:
        want = entry_digest(tool, sentinel, sentinel_disp)
        if str(declared) != want:
            raise ToolDisplayError(
                f"清单头部 `sha256` 与条目对不上：{path} ⇒ 清单被改过（或生成器变了）。"
                f"期望 {want[:12]}…、实为 {str(declared)[:12]}…")
    if sentinel != TOOL_ID_SENTINEL:
        # 出声但**不采纳**：哨兵是工具侧语义常量（`resolve_tool` 全用它），换掉会让所有
        # "机台未记录"的记录变成另一个值 —— 那属于【跨线】契约变更，不是加载器能改的。
        print(f"[机台清单] ⚠️ {path} 的 `sentinel={sentinel!r}` 与本侧语义常量 "
              f"{TOOL_ID_SENTINEL!r} 不一致 ⇒ **不采纳**（哨兵改动属【跨线】契约变更，"
              f"须先与工具线定案）；清单里若真有该键，仍按普通条目加载。", file=sys.stderr)
    mapping: dict[str, str] = {}
    for t in tool:
        mapping[str(t.get("tool_id") or "").strip()] = str(t.get("display") or "").strip()
    return mapping, str(obj.get("mode") or "").strip()


# ---------------------------------------------------------------- 来源解析

def core_dir() -> Path:
    """工作区 core 目录（照 `opennano_config` 的同一开关 —— 不写死路径）。"""
    try:
        from opennano_config import CORE_DIR
        return Path(os.environ.get("OPENNANO_CORE_DIR") or CORE_DIR)
    except Exception:                                    # noqa: BLE001  （评测环境可能没有该模块）
        return Path(os.environ.get("OPENNANO_CORE_DIR") or "")


def candidate_paths() -> list[tuple[Path, bool]]:
    """→ `[(路径, 是否显式指定)]`，**按优先级**。"""
    out: list[tuple[Path, bool]] = []
    env = (os.environ.get("OPENNANO_TOOL_DISPLAY") or "").strip()
    if env:
        out.append((Path(env), True))
    cd = core_dir()
    if str(cd):
        out.append((cd / "machine_tool_display.json", False))
    out.append((DEMO_PATH, False))
    return out


def load() -> tuple[dict[str, str], dict]:
    """→ `(TOOL_DISPLAY, 加载状态)`。**唯一入口**（模块导入时调一次）。

    显式路径/真清单坏 ⇒ 抛；真清单不存在 ⇒ 正常回退 demo；demo 坏 ⇒ 抛。
    """
    for path, explicit in candidate_paths():
        if not path.exists():
            if explicit:
                raise ToolDisplayError(
                    f"`OPENNANO_TOOL_DISPLAY` 指向的清单不存在：{path} ⇒ "
                    f"请修好路径或清掉该环境变量（**不会**静默改用别的来源）。")
            continue
        obj = _read(path)
        mapping, mode = parse(obj, path)
        is_demo = (path == DEMO_PATH) or (mode == "demo")
        info = {
            "source": ("env" if explicit else ("demo" if is_demo else "core")),
            "path": str(path),
            "mode": mode or ("demo" if is_demo else "enforced"),
            "count": len(mapping),
            "error": "",
            #: **不是权威**（样例 / 空）—— 跨线判据据此报「跳过」而不是「通过」
            "degraded": bool(is_demo),
        }
        return mapping, info
    raise ToolDisplayError(
        f"连内置中性样例都读不到：{DEMO_PATH} ⇒ 仓库不完整（缺 `tool_display.demo.json`）。")


TOOL_DISPLAY: dict[str, str]
TOOL_DISPLAY_INFO: dict
TOOL_DISPLAY, TOOL_DISPLAY_INFO = load()


# ---------------------------------------------------------------- 跨线判据（G2 · 三态）

#: `--check` 退出码 —— 与数据线 `ingest/export_tool_display.py --check` **同一套三态约定**：
#: 通过 0 / 失败 1 / **跳过 3**（跳过必须与通过分得开：权威源不可达时"没判"≠"判过了"）
RC_OK, RC_FAIL, RC_SKIP = 0, 1, 3


def authority_path() -> Path:
    """数据线权威源 `core_schema.py` 的位置（照 `opennano_config.DATA_ROOT`，不写死）。"""
    try:
        from opennano_config import DATA_ROOT
        root = Path(os.environ.get("OPENNANO_DATA_ROOT") or DATA_ROOT)
    except Exception:                                    # noqa: BLE001
        root = Path(os.environ.get("OPENNANO_DATA_ROOT") or "")
    return root / "ingest" / "core_schema.py"


def load_authority():
    """只读导入数据线 `core_schema`（**不 import 其模块路径**，避免跨线依赖）→ `(payload, 说明)`。

    不可达 ⇒ `(None, 原因)`，**不猜、不兜底**（缺就是缺，判据要如实报"跳过"）。
    """
    p = authority_path()
    if not str(p) or not p.exists():
        return None, f"权威源不可达：{p}（无 core / 公网 clone / CI）"
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("_core_vocab_authority_probe", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                     # 只读导入，不写任何东西
    except Exception as e:                               # noqa: BLE001
        return None, f"权威源导入失败：{p}（{type(e).__name__}: {e}）"
    td = getattr(mod, "TOOL_DISPLAY", None)
    if not isinstance(td, dict) or not td:
        return None, f"权威源里没有 `TOOL_DISPLAY`：{p}"
    return {
        "tool": list(td.items()),
        "sentinel": getattr(mod, "TOOL_ID_SENTINEL", None),
        "sentinel_display": getattr(mod, "TOOL_UNKNOWN_DISPLAY", None),
        "stages": set(getattr(mod, "STAGES", []) or []),
        "path": str(p),
    }, f"core_schema.TOOL_DISPLAY（{len(td)} 条 · {p}）"


def compare_with_authority(stage_codes=()) -> tuple[int, str]:
    """**G2 判据本体**（供 pytest 与 CLI 共用）→ `(退出码, 说明)`。

    三态：**通过 0**（加载结果 == 权威，含顺序）· **失败 1**（不一致/样例当权威）· **跳过 3**（权威不可达）。
    """
    if TOOL_DISPLAY_INFO.get("degraded"):
        return RC_SKIP, (f"本侧加载的是**中性样例**（mode={TOOL_DISPLAY_INFO.get('mode')} · "
                         f"{TOOL_DISPLAY_INFO.get('path')}）⇒ 样例**不是**权威，"
                         f"本判据**未执行**（跳过 ≠ 通过）")
    payload, why = load_authority()
    if payload is None:
        return RC_SKIP, f"{why} ⇒ 本判据**未执行**（跳过 ≠ 通过）"
    mine = list(TOOL_DISPLAY.items())
    if mine != payload["tool"]:
        wmap, gmap = dict(payload["tool"]), dict(mine)
        miss = [k for k, _ in payload["tool"] if k not in gmap]
        extra = [k for k, _ in mine if k not in wmap]
        drift = [(k, wmap[k], gmap[k]) for k, _ in payload["tool"] if k in gmap and wmap[k] != gmap[k]]
        bits = []
        if miss:
            bits.append(f"本侧缺 {len(miss)} 条：{miss}")
        if extra:
            bits.append(f"本侧多 {len(extra)} 条：{extra}")
        if drift:
            bits.append(f"同键不同名 {len(drift)} 条：{drift}")
        if not (miss or extra or drift):
            bits.append("条目集合相同但**顺序不同**（G2 把顺序算进去）")
        return RC_FAIL, f"不一致（{why}）：" + "；".join(bits)
    if payload["sentinel"] != TOOL_ID_SENTINEL:
        return RC_FAIL, (f"哨兵不一致：我们 {TOOL_ID_SENTINEL!r} ≠ 权威 {payload['sentinel']!r}")
    if payload["sentinel_display"] != TOOL_UNKNOWN_DISPLAY:
        return RC_FAIL, (f"哨兵显示名不一致：我们 {TOOL_UNKNOWN_DISPLAY!r} ≠ "
                         f"权威 {payload['sentinel_display']!r}")
    if stage_codes and payload["stages"] and set(stage_codes) != payload["stages"]:
        only_us = sorted(set(stage_codes) - payload["stages"])
        only_them = sorted(payload["stages"] - set(stage_codes))
        return RC_FAIL, f"stage 词表不一致：多 {only_us} · 少 {only_them}"
    return RC_OK, (f"一致（含顺序）：{len(mine)} 条 · 哨兵 `{TOOL_ID_SENTINEL}` · 来源 "
                   f"{TOOL_DISPLAY_INFO.get('path')} == {why}")


def main(argv=None) -> int:
    """CLI：排障与 CI 可用。`--check` 三态退出码；`--json` 打状态。"""
    import argparse
    ap = argparse.ArgumentParser(description="机台口径表（加载器）现状与跨线判据")
    ap.add_argument("--check", action="store_true",
                    help=f"与数据线权威比对（通过 {RC_OK} / 失败 {RC_FAIL} / 跳过 {RC_SKIP}）")
    ap.add_argument("--json", action="store_true", help="把加载状态打成 JSON")
    a = ap.parse_args(argv)
    if a.check:
        rc, msg = compare_with_authority()
        print(f"[{'通过' if rc == RC_OK else ('失败' if rc == RC_FAIL else '跳过')}] {msg}")
        return rc
    st = status()
    if a.json:
        print(json.dumps({**st, "tool": TOOL_DISPLAY}, ensure_ascii=False, indent=2))
        return RC_OK
    print(f"口径表：{st['count']} 条 · 来源 {st['source']}（{st['mode']}）· {st['path']}")
    print(f"  {st['note']}")
    print(f"  权威：{st['authority']}")
    rc, msg = compare_with_authority()
    print(f"  跨线判据：{'通过' if rc == RC_OK else ('失败' if rc == RC_FAIL else '跳过')} —— {msg}")
    return rc



def status() -> dict:
    """给 `/api/health` 与排障用（**永不抛**）：口径表从哪儿来、是不是权威、几条。"""
    st = dict(TOOL_DISPLAY_INFO)
    st.update({
        "ok": not st.get("degraded", False),
        "authority": "core_schema.TOOL_DISPLAY（数据线）—— 本侧只加载其派生物",
        "note": ("**中性样例**：公开 clone / CI 用（真清单不在仓库里）" if st.get("degraded")
                 else "加载自真清单"),
        "candidates": [str(p) for p, _ in candidate_paths()],
    })
    return st


# ---------------------------------------------------------------- 消费侧（口径解析）

def tool_display(tool_id: str) -> str:
    """`tool_id` → 唯一显示名；**未登记返回空串**（调用方必须自己兜底，不许留空）。"""
    return TOOL_DISPLAY.get((tool_id or "").strip(), "")


def _machine_match(m: dict, machines: list[dict]) -> dict | None:
    """画布模块 → 应用库里的机台档案。**先认 id，再认 name**（id 是我们自己发的，最硬）。"""
    mid = (m.get("machine_id") or "").strip()
    name = (m.get("machine_name") or "").strip()
    by_name = None
    for mc in machines or []:
        if mid and (mc.get("id") or "") == mid:
            return mc
        if name and (mc.get("name") or "") == name and by_name is None:
            by_name = mc
    return by_name


def resolve_tool(m: dict, machines: list[dict] | None = None,
                 stage_codes=()) -> tuple[str, str, str]:
    """画布模块 → `(tool_id, tool 显示名, 告警)`。**导出侧唯一入口**（`expack` / `append_pack` 共用）。

    `tool_id` 解析顺序：
      1. `m["core_tool_id"]` —— 从 core 导入时写入（与 `core_run_id`/`core_recipe_id` 同一套往返）。
         **core 的记录优先**：已入库的 run，机台就是 core 里那台，画布上改机台不改记录。
         ⚠️ 这一路**照抄、不校验登记**：它是**记录**（未登记也是 core 自己的事，由数据线的闸报出来），
         我们不能替 core 改记录；
      2. 应用库机台档案的 `tool_id` —— 画布上新选的机台。⚠️ **只认已登记的**（`∈ TOOL_DISPLAY`）：
         库内标签**不是** core 口径，冒充就是编（实测库里真有未登记的画布名）。
         若数据线闸 ⑤ 拒收未登记 `tool_id`，冒充的结果是**整包被拒**；
      3. 哨兵 `UNKNOWN`（**不留空** —— 空的语义是"漏填"，与"机台未记录"必须分得开）。
    `stage_codes` 非空时再兜一道：**撞 stage 词的绝不写进 `tool_id`**（那是"把工序名当机台号"）。

    显示名解析顺序：core 原值（`core_tool`，且必须与最终 `tool_id` 同源）→ `TOOL_DISPLAY`
    → 库内机台名 → 哨兵显示名。**一个 `tool_id` 在一个包里只能有一个显示名**（数据线机台闸 ③）。

    第三条 = 人类可读告警（空串＝无话说）。**调用方必须把它带出去**（卡 / manifest / 摘要），
    否则"机台没登记"这件事就是静默的 —— 那正是这套闸要治的病。
    """
    machines = machines or []
    core_tid = (m.get("core_tool_id") or "").strip()
    core_name = (m.get("core_tool") or "").strip()
    mc = None
    note = ""
    tid = core_tid
    if not tid:
        mc = _machine_match(m, machines)
        cand = (mc.get("tool_id") or "").strip() if mc else ""
        if cand and cand in TOOL_DISPLAY and not (stage_codes and cand in stage_codes):
            tid = cand
        else:
            tid = TOOL_ID_SENTINEL
            if mc is not None:
                label = (mc.get("name") or "").strip() or "（无名机台）"
                if not cand:
                    note = (f"机台 `{label}` 在应用库里没填 `tool_id`（core 机台号）⇒ 本 run 的 "
                            f"`tool_id` 落哨兵 `{TOOL_ID_SENTINEL}`")
                else:
                    note = (f"机台 `{label}` 的 `tool_id='{cand}'` **不在 core 的 `TOOL_DISPLAY` 里**"
                            f"（数据线机台闸 ⑤ 会拒收）⇒ 本 run 的 `tool_id` 落哨兵 `{TOOL_ID_SENTINEL}`；"
                            f"要用真机台号请先把它登记进 `core_schema.TOOL_DISPLAY`")
    elif stage_codes and tid in stage_codes:
        # core 原值撞 stage 词（历史遗留/外部包）：仍不许写出去（② 是**格式**问题，与登记无关）
        note = f"`core_tool_id='{tid}'` 撞 stage 代号 ⇒ 改落哨兵 `{TOOL_ID_SENTINEL}`"
        tid = TOOL_ID_SENTINEL
    if not tid:
        tid = TOOL_ID_SENTINEL
    # 显示名：core 原值只在"就是 core 那个 tool_id"时才算数（换了机台 ⇒ 旧显示名不许跟过来）
    name = core_name if (core_tid and tid == core_tid and core_name) else ""
    if not name:
        name = tool_display(tid)
    if not name and tid == TOOL_ID_SENTINEL:
        # ⚠️ 哨兵显示名**不依赖清单里有没有那一行**：清单是数据，可能漏写或被人手改，
        #    而"机台未记录"的显示名是**语义常量** ⇒ 永远有确定值（2026-09-26 实测踩到：
        #    清单缺哨兵行时未登记机台会退回画布机台名，看着像"机台记上了"，与落哨兵自相矛盾）。
        name = TOOL_UNKNOWN_DISPLAY
    if not name and mc is not None:
        name = (mc.get("name") or "").strip()
    return tid, (name or TOOL_UNKNOWN_DISPLAY), note
if __name__ == "__main__":
    raise SystemExit(main())
