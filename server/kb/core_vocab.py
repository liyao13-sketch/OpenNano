"""core 契约里「机台口径」的字面量与解析 —— **工具侧**（导出/消费都走这里）。

⚠️ **这是镜像，不是真源**。真源在数据线：
   `个人空间/18_工艺数据资产/03_实验数据/ingest/core_schema.py`
   （`TOOL_ID_SENTINEL` / `TOOL_UNKNOWN_DISPLAY` / `TOOL_DISPLAY`，2026-09-14 立）
为什么镜像而不 import：跨线只共享**契约**、不共享模块路径 —— `18_工艺数据资产/` 是数据线的地，
工具侧运行时不依赖它（评测/CI 环境里也未必在场）。
镜像的代价是会漂移 ⇒ 配一条**跨线逐字判据**
（`tests/test_tool_identity.py::test_cross_line_tool_display_is_byte_identical`），任一侧改了另一边就红。

## 这个文件为什么存在（2026-09-14）

数据线用**我们的真导出代码**造真包喂 `build_core` 联调，整包被拦，拦点是**机台闸**（不是 stage 闸）：
  ① 检测节点（metrology）导出的 `tool_id` 是**空**；
  ② `PECVD` 模块导出的 `tool_id='PECVD'` —— 那是 **stage 名**；
  ③ 同一个 `tool_id` 在包内出现**两个显示名**。
根因：导出侧只认画布字段 `machine_name`（**应用库里的显示名**，如 `DRIE-Bosch` / `PECVD` / `ICP-鲁汶`），
从不 round-trip core 的 `tool_id`/`tool`。
⚠️ 比"被闸拦住"更值得记的是：**闸只拦格式，拦不住"错机台"** —— `tool_id='DRIE-Bosch'`（真值
`RIE-400iPB`）与 `tool_id='ICP-鲁汶'`（真值 `ICP-PishowA`）都**长得像合法值**，闸一条都不报，
会**静默入库**、把机台归属记错。所以修的是口径，不只是"让闸放行"。

解析顺序见 `resolve_tool`；零号铁律：**不猜、不留空、不写 stage 名**。
"""
from __future__ import annotations

#: 「机台未记录 / 尚未定」的唯一哨兵（逐字镜像 `core_schema.TOOL_ID_SENTINEL`）
TOOL_ID_SENTINEL = "UNKNOWN"

#: 哨兵对应的唯一显示名（逐字镜像 `core_schema.TOOL_UNKNOWN_DISPLAY`）
TOOL_UNKNOWN_DISPLAY = "UNKNOWN（机台未记录）"

#: `tool_id` → 唯一显示名（逐字镜像 `core_schema.TOOL_DISPLAY`；**顺序也算**，跨线判据会比对）
TOOL_DISPLAY: dict[str, str] = {
    "RIE200NL": "SAMCO RIE200NL（氯基）",
    "RIE10NR": "SAMCO RIE10NR（氟基）",
    "RIE-400iPB": "SAMCO RIE-400iPB（Bosch）",
    "ICP-PishowA": "Hassrode PishowA",
    "PECVD-SAMCO": "SAMCO PD-220NL（PECVD）",      # 内部设备总表 §三：SAMCO PD-220NL
    "DWL66": "Heidelberg DWL66（激光直写）",        # 内部设备总表 §一
    "EBPG5200": "Raith EBPG 5200（EBL）",          # 内部设备总表 §一
    "SPUTTER": "JSP-4（磁控溅射）",                 # 内部设备总表 §三（四腔磁控溅射 JSP-4）
    # 2026-09-15 数据线登记 4 台（`TOOL_DISPLAY` 9→13）——**源于本单联调查出的"库内未登记机台"**：
    #   · `RIBE-LoremR` 命名镜像 `ICP-PishowA`（工序-型号），依据 内部设备总表 §2b；
    #   · `SUSS-MA6`   ⚠️ **不能叫 `MA6`**：撞 stage 代号，会被数据线闸 ② 当"把工序名当机台号"拦下（我提的，他们采纳）；
    #   · `SI500`      依据 设备总表（SENTECH SI500）；
    #   · `FST5000`    依据 core 自己的 `measurements.csv` note（"压应力；FST5000"）。
    #   ⚠️ `CD-SEM` / `椭偏仪` **刻意不登记**（型号未核实，内部设备总表 §四 写"型号待补"）：
    #      它们导出会落哨兵 + 出 `unregistered_machine` 告警 —— 宁可记"机台未记录"，不写没核实过的型号。
    "RIBE-LoremR": "Hassrode Lorem R（RIBE）",
    "SI500": "SENTECH SI500（ICP-RIE）",
    "SUSS-MA6": "SUSS MA6（掩模对准曝光）",
    "FST5000": "FST5000（薄膜应力）",
    TOOL_ID_SENTINEL: TOOL_UNKNOWN_DISPLAY,
}


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
         库内标签**不是** core 口径，冒充就是编（实测库里真有未登记的：`RIBE-鲁汶`/`MA6`）。
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
    if not name and mc is not None:
        name = (mc.get("name") or "").strip()
    return tid, (name or TOOL_UNKNOWN_DISPLAY), note
