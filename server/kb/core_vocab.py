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
                 stage_codes=()) -> tuple[str, str]:
    """画布模块 → `(tool_id, tool 显示名)`。**导出侧唯一入口**（`expack` / `append_pack` 共用）。

    `tool_id` 解析顺序：
      1. `m["core_tool_id"]` —— 从 core 导入时写入（与 `core_run_id`/`core_recipe_id` 同一套往返）。
         **core 的记录优先**：已入库的 run，机台就是 core 里那台，画布上改机台不改记录；
      2. 应用库机台档案的 `tool_id` —— 画布上新选的机台（这是库与 core 的共同口径）；
      3. 哨兵 `UNKNOWN`（**不留空** —— 空的语义是"漏填"，与"机台未记录"必须分得开）。
    `stage_codes` 非空时再兜一道：**撞 stage 词的绝不写进 `tool_id`**（那是"把工序名当机台号"）。

    显示名解析顺序：core 原值（`core_tool`，且必须与最终 `tool_id` 同源）→ `TOOL_DISPLAY`
    → 库内机台名 → 哨兵显示名。**一个 `tool_id` 在一个包里只能有一个显示名**（数据线机台闸 ③）。
    """
    machines = machines or []
    core_tid = (m.get("core_tool_id") or "").strip()
    core_name = (m.get("core_tool") or "").strip()
    mc = None
    tid = core_tid
    if not tid:
        mc = _machine_match(m, machines)
        tid = (mc.get("tool_id") or "").strip() if mc else ""
    if not tid or (stage_codes and tid in stage_codes):
        tid = TOOL_ID_SENTINEL
    # 显示名：core 原值只在"就是 core 那个 tool_id"时才算数（换了机台 ⇒ 旧显示名不许跟过来）
    name = core_name if (core_tid and tid == core_tid and core_name) else ""
    if not name:
        name = tool_display(tid)
    if not name and mc is not None:
        name = (mc.get("name") or "").strip()
    return tid, (name or TOOL_UNKNOWN_DISPLAY)
