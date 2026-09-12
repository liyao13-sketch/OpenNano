"""画布几何的**唯一来源**（节点尺寸 / 间距 / 高度估算）。

为什么要单独一个模块：同一组数字被**三处**引用 ——
    · 前端 `ProcessNode`（TSX 里的宽 190、内边距、芯片行、备注限高）
    · 后端布局 `expack._layout_modules`（列距/行距）
    · 体检 `layout_audit`（判重叠）
过去它们各写各的 ⇒ 前端改了高度、后端还按旧尺寸排 ⇒ 出现"方块叠在一起"这类问题。
现在：**后端两处共用本模块**；前端 TSX 里的数字必须与本文件保持一致（改动时同步，见下方注释）。

口径（2026-09-13 owner："横纵两个方向间距等宽 + 按方块布局自适应"）：
    · **间距 GAP 是同一个数**：横向 = 列距 − 节点宽；纵向 = 行距 − 该行最高节点高；
    · **自适应**：行距按"这一行里最高的那个节点"算 ⇒ 有备注的行自动加高，没备注的行不加高；
    · 备注按**前端限高后的最坏情况**（3 行）计入 ⇒ 用户开关"显示备注"都不会再压到下一格。
"""
from __future__ import annotations

#: 节点宽度（与前端 ProcessNode 的 `width:190` 一致）
NODE_W = 190
#: 无备注、无芯片行时的基础高度（标题 + 副标题 + 上下内边距 ≈ 69）
NODE_BASE_H = 71
#: 芯片行（run 短号 / sample / #工序号）高度
CHIP_H = 18
#: 备注块：每行高度 + 上下额外（内边距与边框）
COMMENT_LINE_H = 16
COMMENT_EXTRA = 12
#: 前端备注**限高**行数（`-webkit-line-clamp: 3`）—— 体检与布局都按这个上限算
CLAMP_COMMENT_LINES = 1   # 前端已把备注收成恒定 1 行（省略号 + 悬浮看全文）
#: **统一间距**：横纵都用它（等宽）。
#: 96 → 72 是 2026-09-13 owner「画布不够紧凑」后的收紧；仍满足「横纵相等」这条要求，
#: 改这一个数，后端布局与体检器同时生效（前端只引用同一套几何）。
GAP = 72
#: 起点
X0, Y0 = 140, 80
#: 兼容旧名：标称列距（= 节点宽 + 统一间距）
COL_PITCH = NODE_W + GAP


def comment_lines_of(m: dict, shown: bool = True) -> int:
    """该模块备注会占几行（按**限高后的上限**算）。"""
    if not shown or not (m.get("comment") or "").strip():
        return 0
    # 前端把备注渲染成**恒定 1 行**（省略号截断、悬浮看全文）⇒ 高度与备注长短无关，
    # 布局因此可以紧凑：这是"画布不够紧凑、字被缩得很小"的根治点。
    return CLAMP_COMMENT_LINES


def node_height(m: dict, comments_shown: bool = True) -> int:
    """节点渲染高度估算（与前端一致的模型）。"""
    h = NODE_BASE_H
    if m.get("core_run_id") or m.get("run_nature"):
        h += CHIP_H
    lines = comment_lines_of(m, comments_shown)
    if lines:
        h += COMMENT_EXTRA + lines * COMMENT_LINE_H
    return h


def gaps(project: dict, comments_shown: bool = True) -> dict:
    """量一张画布现成的横纵间距（给体检用）。

    - 横向：相邻两列的 x 差 − 节点宽；
    - 纵向：同一列里相邻两节点的 y 差 − **上面那个节点的高度**（这才是"视觉缝"）。
    """
    mods = project.get("modules") or []
    xs = sorted({float(m.get("x") or 0) for m in mods})
    # 按 y 排序（只比 y；同 y 时不能拿 dict 比大小，否则 TypeError）
    cols = {x: sorted(((float(m.get("y") or 0), i, m) for i, m in enumerate(mods)
                       if float(m.get("x") or 0) == x), key=lambda p: (p[0], p[1]))
            for x in xs}
    h_gaps = [round(b - a - NODE_W, 1) for a, b in zip(xs, xs[1:])]
    v_gaps: list[float] = []
    for x, items in cols.items():
        for (y1, _i1, m1), (y2, _i2, _m2) in zip(items, items[1:]):
            v_gaps.append(round(y2 - y1 - node_height(m1, comments_shown), 1))
    return {"h_gaps": h_gaps, "v_gaps": v_gaps}
