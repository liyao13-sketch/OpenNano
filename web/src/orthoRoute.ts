// 用法：见 App.tsx 的 edgeOf / 自定义 edge 组件 —— 把 routeEdge() 的结果拼成 SVG path
/**
 * orthoRoute.ts —— 正交连线路由器（零依赖纯 TypeScript 模块）
 *
 *   import { routeEdge, type Pt } from './orthoRoute'
 *
 *   const pts = routeEdge({
 *     source:    { x: sx, y: sy, w: sw, h: sh },   // 画布坐标，源节点矩形
 *     target:    { x: tx, y: ty, w: tw, h: th },   // 画布坐标，目标节点矩形
 *     obstacles: allNodeBoxes,                     // 所有节点矩形（含 source/target，内部自行排除）
 *     taken:     alreadyRouted.map(e => ({ points: e.points })),  // 已排好的其它连线（用于错开）
 *     portOffset: 0,                               // 端口相对垂直中心的偏移（默认 0＝居中）
 *     margin: 6,                                   // 障碍外扩量（默认 6px）
 *   })
 *   const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
 *   // <path d={d} fill="none" stroke="..." markerEnd="url(#arrow)" />
 *
 * 算法（与画布的网格布局一致）：
 *   1. 从 obstacles 反推列/行：把方块的 x 区间、y 区间按「重叠即并」聚成**列簇 / 行簇**。
 *      相邻簇之间的竖直缝 / 水平缝取中点 ⇒ 候选走廊线。这些线**整条都是空的**
 *      （没有任何方块的 x/y 区间跨过缝的中点），所以「缝 × 缝」构成的走廊网格
 *      天生连通、天生不撞方块 —— 这就是布局给我们的天然走廊。
 *   2. 再补：每个列簇 / 行簇两侧、外扩 margin + OUTER_CLEAR 的兜底走廊，
 *      以及最外侧的全局兜底走廊；最后强制加入源端口 x、目标端口 x、源端口 y、目标端口 y。
 *   3. 在 (候选 x × 候选 y) 网格上跑 **A\***：状态 = (格点, 进入方向)，
 *      代价 = 曼哈顿长度 + 拐弯代价（TURN_COST ≫ 任何长度）
 *      ⇒ **先最少拐弯、再最短长度**。每条候选边都拿**外扩后的矩形**做碰撞判定，
 *      所以走出来的路一定不穿方块；「出源 → 在源右侧缝里竖直走 → 进目标」的 2 拐
 *      3 段路由只要是空的，A* 一定会优先选它。
 *   4. 错开（taken）：在 ±10px、2px 步长内平移竖直段的 x，取**与已排连线距离最大**者；
 *      全程无随机数 / 无时间 ⇒ 相同输入必定相同输出。
 *
 * 保证：**绝不抛异常**（内部任何异常都退到 fallbackRoute 的 3 段兜底折线）。
 */

export interface Pt { x: number; y: number }
export interface Box { x: number; y: number; w: number; h: number }
export interface RouteRequest {
  source: Box                 // 源节点矩形（画布坐标）
  target: Box                 // 目标节点矩形
  obstacles: Box[]            // 所有节点矩形（含 source/target，实现内部自行排除这两个）
  taken: { points: Pt[] }[]   // 已经排好的其它连线路径（用于错开，避免重合叠线）
  portOffset?: number         // 端口相对垂直中心的偏移，默认 0（居中）
  margin?: number             // 障碍外扩量，默认 6
}

/* ------------------------------------------------------------------ 常量 */

const EPS = 1e-6
/** 拐弯代价：远大于任何路径总长 ⇒ 满足「先最少拐弯，再最短长度」 */
const TURN_COST = 1e6
/** A* 展开状态上限 —— 搜索节点数有硬上限，不可能指数爆炸 */
const MAX_POPS = 20000
/** 兜底走廊：在 margin 之外再让出的余量 */
const OUTER_CLEAR = 14
/** 错开微调：±10px、2px 步长（顺序固定 ⇒ 确定性） */
const NUDGE: number[] = [0, 2, -2, 4, -4, 6, -6, 8, -8, 10, -10]
/** 同一簇内两条候选线的最小间隔（小于它视为同一条线） */
const LINE_MERGE = 0.75
/** 簇数超过它就进入"稀疏候选线"模式（只留缝中点 + 全局兜底） */
const MANY_CLUSTERS = 32

interface Rect { x0: number; y0: number; x1: number; y1: number }

interface Grid {
  nx: number
  ny: number
  xs: number[]
  ys: number[]
  rects: Rect[]
  /** 竖段（x 固定）可能撞到的矩形下标 */
  hitX: number[][]
  /** 横段（y 固定）可能撞到的矩形下标 */
  hitY: number[][]
}

/* --------------------------------------------------------------- 小工具 */

function numOr(v: any, d: number): number {
  return typeof v === 'number' && isFinite(v) ? v : d
}

function isFiniteBox(b: any): boolean {
  return !!b && typeof b === 'object'
    && typeof b.x === 'number' && isFinite(b.x)
    && typeof b.y === 'number' && isFinite(b.y)
    && typeof b.w === 'number' && isFinite(b.w)
    && typeof b.h === 'number' && isFinite(b.h)
}

function sameBox(a: any, b: any): boolean {
  if (!a || !b) return false
  if (a === b) return true
  return Math.abs(a.x - b.x) < EPS && Math.abs(a.y - b.y) < EPS
    && Math.abs(a.w - b.w) < EPS && Math.abs(a.h - b.h) < EPS
}

/* ----------------------------------------------------------- 主入口 */

/**
 * 计算一条从 source 右边中点到 target 左边中点的正交折线。
 * 首点＝源端口，末点＝目标端口；无重复点、无零长线段；不会穿过任何（按 margin 外扩的）障碍。
 * 找不到合法路径时返回 3 段兜底折线（见 fallbackRoute），**永不抛异常**。
 */
export function routeEdge(req: RouteRequest): Pt[] {
  const r: any = req || {}
  const sb: Box = isFiniteBox(r.source) ? r.source : { x: 0, y: 0, w: 0, h: 0 }
  const tb: Box = isFiniteBox(r.target) ? r.target : { x: 0, y: 0, w: 0, h: 0 }
  const margin = numOr(r.margin, 6)
  const portOffset = numOr(r.portOffset, 0)
  const S: Pt = { x: sb.x + sb.w, y: sb.y + sb.h / 2 + portOffset }
  const T: Pt = { x: tb.x, y: tb.y + tb.h / 2 + portOffset }

  try {
    const pts = routeInner(r, sb, tb, S, T, margin)
    if (pts && pts.length >= 1) return pts
  } catch (err) {
    /* 任何内部异常都不许冒泡 —— 落到兜底 */
  }
  return fallbackRoute(S, T)
}

/* ------------------------------------------------------- 内部主流程 */

function routeInner(req: any, sb: Box, tb: Box, S: Pt, T: Pt, margin: number): Pt[] {
  /* 1. 碰撞用矩形：外扩 margin，并排除 source / target 两个方块 */
  const rects: Rect[] = []
  const all: Box[] = []
  const raw = Array.isArray(req.obstacles) ? req.obstacles : []
  for (const b of raw) {
    if (!isFiniteBox(b) || b.w <= 0 || b.h <= 0) continue
    all.push(b)
    if (sameBox(b, sb) || sameBox(b, tb)) continue
    rects.push({ x0: b.x - margin, y0: b.y - margin, x1: b.x + b.w + margin, y1: b.y + b.h + margin })
  }

  /* 2. 走廊聚类要认得 source / target 所在的列与行 */
  const clusterBoxes: Box[] = all.slice()
  if (sb.w > 0 && sb.h > 0) clusterBoxes.push(sb)
  if (tb.w > 0 && tb.h > 0) clusterBoxes.push(tb)

  const xs = buildLines(clusterBoxes, 0, margin, [S.x, T.x])
  const ys = buildLines(clusterBoxes, 1, margin, [S.y, T.y])
  const si = indexOfVal(xs, S.x), sj = indexOfVal(ys, S.y)
  const ti = indexOfVal(xs, T.x), tj = indexOfVal(ys, T.y)
  if (si < 0 || sj < 0 || ti < 0 || tj < 0) return fallbackRoute(S, T)

  /* 3. 走廊网格 + A* */
  const grid = buildGrid(xs, ys, rects)
  const rawPath = aStar(grid, si, sj, ti, tj)
  if (!rawPath) return fallbackRoute(S, T)

  let pts = normalize(rawPath)
  if (!wellFormed(pts) || !polylineFree(pts, rects)) return fallbackRoute(S, T)

  /* 4. 与 taken 错开：微调竖直段 x，取与 taken 最不叠的那条 */
  const taken = collectTaken(req.taken)
  if (taken.length) {
    const flat = flattenTaken(taken)
    if (flat.length) {
      const best = nudge(pts, rects, flat)
      if (best && wellFormed(best) && polylineFree(best, rects)) pts = normalize(best)
    }
  }
  return pts
}

/**
 * 兜底折线（**只在这里用**）：源端口 → 源/目标之间的中点 x → 目标端口。
 * 只要 A* 找不到合法正交路（例如端口被别的方块外扩矩形压住、或搜索超上限）就返回它 ——
 * 这样 routeEdge 绝不抛异常、也绝不断线。
 * 注意：兜底本身**可能穿过方块**（那种情形下不存在既保端点又不穿方块的正交路），
 *       调用方若需要更严格的保证，应把这种退化布局在数据层就避开。
 */
function fallbackRoute(S: Pt, T: Pt): Pt[] {
  const mx = (S.x + T.x) / 2
  return normalize([S, { x: mx, y: S.y }, { x: mx, y: T.y }, T])
}

/* --------------------------------------------------- 走廊线 / 网格 */

/**
 * 把一轴上的区间按「重叠或相接即并」聚成簇，返回：
 *   · 相邻簇**缝的中点**（★ 主走廊：这些线整条都是空的）
 *   · 每个簇左右/上下两侧、外扩 margin + OUTER_CLEAR 的兜底走廊
 *   · 最外侧的全局兜底走廊
 * 簇特别多时（≤ MANY_CLUSTERS）只留缝中点 + 全局兜底，避免候选线爆到几百条
 * —— 连通性只依赖缝中点，丢掉的只是"更贴近某列的备选车道"。
 */
function corridorLines(boxes: Box[], axis: number, margin: number): number[] {
  const iv: number[][] = []
  for (const b of boxes) {
    const lo = axis === 0 ? b.x : b.y
    const hi = lo + (axis === 0 ? b.w : b.h)
    if (hi - lo > EPS) iv.push([lo, hi])
  }
  if (!iv.length) return []
  iv.sort((p, q) => (p[0] - q[0]) || (p[1] - q[1]))
  const cl: number[][] = []
  for (const s of iv) {
    const last = cl[cl.length - 1]
    if (last && s[0] <= last[1] + EPS) { if (s[1] > last[1]) last[1] = s[1] }
    else cl.push([s[0], s[1]])
  }
  const dense = cl.length > MANY_CLUSTERS
  const out = margin + OUTER_CLEAR
  const lines: number[] = []
  lines.push(cl[0][0] - out)                                  // 全局外侧兜底走廊
  for (let k = 0; k + 1 < cl.length; k++) {
    lines.push((cl[k][1] + cl[k + 1][0]) / 2)                 // ★ 缝的中点（保证整条为空）
    if (!dense) {
      lines.push(cl[k][1] + out)                              // 本簇右侧外侧兜底走廊
      lines.push(cl[k + 1][0] - out)                          // 下一簇左侧外侧兜底走廊
    }
  }
  lines.push(cl[cl.length - 1][1] + out)                      // 全局外侧兜底走廊
  return lines
}

/** 去重排序；tol 内视为同一条线 */
function uniqSorted(a: number[], tol: number): number[] {
  const v = a.filter(x => typeof x === 'number' && isFinite(x)).sort((p, q) => p - q)
  const out: number[] = []
  for (const x of v) {
    if (!out.length || Math.abs(x - out[out.length - 1]) > tol) out.push(x)
  }
  return out
}

/** 候选线 + 强制线（端口必须精确落在网格线上） */
function buildLines(boxes: Box[], axis: number, margin: number, forced: number[]): number[] {
  let out = uniqSorted(corridorLines(boxes, axis, margin), LINE_MERGE)
  for (const f of forced) {
    if (typeof f !== 'number' || !isFinite(f)) continue
    let hit = -1
    for (let k = 0; k < out.length; k++) if (Math.abs(out[k] - f) <= LINE_MERGE) { hit = k; break }
    if (hit >= 0) out[hit] = f
    else out.push(f)
  }
  return uniqSorted(out, 0)
}

function indexOfVal(a: number[], v: number): number {
  for (let i = 0; i < a.length; i++) if (a[i] === v) return i
  return -1
}

function buildGrid(xs: number[], ys: number[], rects: Rect[]): Grid {
  const nx = xs.length, ny = ys.length
  const hitX: number[][] = new Array(nx)
  for (let i = 0; i < nx; i++) {
    const x = xs[i], list: number[] = []
    for (let k = 0; k < rects.length; k++) { const r = rects[k]; if (x >= r.x0 && x <= r.x1) list.push(k) }
    hitX[i] = list
  }
  const hitY: number[][] = new Array(ny)
  for (let j = 0; j < ny; j++) {
    const y = ys[j], list: number[] = []
    for (let k = 0; k < rects.length; k++) { const r = rects[k]; if (y >= r.y0 && y <= r.y1) list.push(k) }
    hitY[j] = list
  }
  return { nx, ny, xs, ys, rects, hitX, hitY }
}

/** 横段 xs[i]→xs[i+1] @ y=ys[j] 是否空（不碰任何外扩矩形） */
function freeH(g: Grid, i: number, j: number): boolean {
  if (i < 0 || i + 1 >= g.nx) return false
  const lo = g.xs[i], hi = g.xs[i + 1]
  if (!(hi - lo > EPS)) return false
  const list = g.hitY[j]
  for (let k = 0; k < list.length; k++) {
    const r = g.rects[list[k]]
    if (hi > r.x0 && lo < r.x1) return false
  }
  return true
}

/** 竖段 ys[j]→ys[j+1] @ x=xs[i] 是否空 */
function freeV(g: Grid, i: number, j: number): boolean {
  if (j < 0 || j + 1 >= g.ny) return false
  const lo = g.ys[j], hi = g.ys[j + 1]
  if (!(hi - lo > EPS)) return false
  const list = g.hitX[i]
  for (let k = 0; k < list.length; k++) {
    const r = g.rects[list[k]]
    if (hi > r.y0 && lo < r.y1) return false
  }
  return true
}

/* --------------------------------------------------------------- A* */

interface Heap { f: number[]; id: number[] }

function hpush(h: Heap, f: number, id: number): void {
  h.f.push(f); h.id.push(id)
  let i = h.f.length - 1
  while (i > 0) {
    const p = (i - 1) >> 1
    if (h.f[p] < h.f[i] || (h.f[p] === h.f[i] && h.id[p] <= h.id[i])) break
    const tf = h.f[p]; h.f[p] = h.f[i]; h.f[i] = tf
    const ti = h.id[p]; h.id[p] = h.id[i]; h.id[i] = ti
    i = p
  }
}

function hpop(h: Heap): number {
  const n = h.f.length
  if (n === 0) return -1
  const top = h.id[0]
  const lf = h.f.pop() as number, li = h.id.pop() as number
  if (n > 1) {
    h.f[0] = lf; h.id[0] = li
    let i = 0
    for (;;) {
      const l = 2 * i + 1, r = l + 1
      let m = i
      if (l < h.f.length && (h.f[l] < h.f[m] || (h.f[l] === h.f[m] && h.id[l] < h.id[m]))) m = l
      if (r < h.f.length && (h.f[r] < h.f[m] || (h.f[r] === h.f[m] && h.id[r] < h.id[m]))) m = r
      if (m === i) break
      const tf = h.f[m]; h.f[m] = h.f[i]; h.f[i] = tf
      const ti = h.id[m]; h.id[m] = h.id[i]; h.id[i] = ti
      i = m
    }
  }
  return top
}

/** 四个方向：右、左、下、上（固定顺序 ⇒ 确定性） */
const DIRS: number[][] = [[1, 0], [-1, 0], [0, 1], [0, -1]]

/**
 * A* over (候选x × 候选y) 网格，状态 = 格点 × 进入方向（0=横, 1=竖）。
 * 代价 = 长度 + TURN_COST×拐弯数 ⇒ 先最少拐弯、再最短长度。
 * 启发 = 曼哈顿距离（可采纳：任何路径长度 ≥ 曼哈顿距离）。
 */
function aStar(g: Grid, si: number, sj: number, ti: number, tj: number): Pt[] | null {
  const nx = g.nx, ny = g.ny
  const N = nx * ny
  const gCost = new Float64Array(N * 2).fill(Infinity)
  const cameFrom = new Int32Array(N * 2).fill(-1)
  const closed = new Uint8Array(N * 2)
  const heap: Heap = { f: [], id: [] }
  const goalNode = ti * ny + tj
  const startNode = si * ny + sj
  const gx = g.xs[ti], gy = g.ys[tj]
  const hOf = (i: number, j: number) => Math.abs(g.xs[i] - gx) + Math.abs(g.ys[j] - gy)

  /* 两个方向都以 0 代价起步 ⇒ 第一段不算拐弯 */
  for (let d = 0; d < 2; d++) {
    const st = startNode * 2 + d
    gCost[st] = 0
    hpush(heap, hOf(si, sj), st)
  }

  let pops = 0
  while (heap.f.length) {
    const st = hpop(heap)
    if (st < 0) break
    if (closed[st]) continue
    closed[st] = 1
    if (++pops > MAX_POPS) return null

    const node = st >>> 1, dir = st & 1
    if (node === goalNode) return rebuild(cameFrom, st, g)

    const i = (node / ny) | 0, j = node - i * ny
    for (let k = 0; k < 4; k++) {
      const di = DIRS[k][0], dj = DIRS[k][1]
      const ni = i + di, nj = j + dj
      if (ni < 0 || ni >= nx || nj < 0 || nj >= ny) continue
      /* 不许朝左穿出源方块（端口在源右边界）；也不许从右侧穿进目标方块 */
      if (di < 0 && i === si && j === sj) continue
      if (di < 0 && ni === ti && nj === tj) continue
      /* 端口所在的 x 线（源右边界 / 目标左边界）上不许竖直走：
         否则一条扇出的边都会贴着节点边缘走，彼此完全叠死（错开也无从下手）。
         竖直走一律交给列间缝 / 簇外兜底走廊。 */
      if (dj !== 0 && (i === si || i === ti)) continue
      const moveDir = di !== 0 ? 0 : 1
      let len: number, ok: boolean
      if (di !== 0) { len = Math.abs(g.xs[ni] - g.xs[i]); ok = freeH(g, Math.min(i, ni), j) }
      else { len = Math.abs(g.ys[nj] - g.ys[j]); ok = freeV(g, i, Math.min(j, nj)) }
      if (!ok || !(len > EPS)) continue
      const ns = (ni * ny + nj) * 2 + moveDir
      if (closed[ns]) continue
      const ng = gCost[st] + len + (dir === moveDir ? 0 : TURN_COST)
      if (ng < gCost[ns] - 1e-9) {
        gCost[ns] = ng
        cameFrom[ns] = st
        hpush(heap, ng + hOf(ni, nj), ns)
      }
    }
  }
  return null
}

function rebuild(from: Int32Array, st: number, g: Grid): Pt[] {
  const pts: Pt[] = []
  let cur = st, guard = 0
  while (cur >= 0 && guard++ < 100000) {
    const node = cur >>> 1
    const i = (node / g.ny) | 0, j = node - i * g.ny
    pts.push({ x: g.xs[i], y: g.ys[j] })
    cur = from[cur]
  }
  pts.reverse()
  return pts
}

/* --------------------------------------------------- 折线整理 / 校验 */

/** 去掉连续重复点、合并共线（不许有零长线段） */
function normalize(pts: Pt[]): Pt[] {
  const a: Pt[] = []
  for (const p of pts) {
    if (!p || !isFinite(p.x) || !isFinite(p.y)) continue
    const last = a[a.length - 1]
    if (last && Math.abs(last.x - p.x) < 1e-9 && Math.abs(last.y - p.y) < 1e-9) continue
    a.push({ x: p.x, y: p.y })
  }
  const out: Pt[] = []
  for (const p of a) {
    while (out.length >= 2) {
      const q = out[out.length - 2], r = out[out.length - 1]
      const cross = (r.x - q.x) * (p.y - q.y) - (r.y - q.y) * (p.x - q.x)
      if (Math.abs(cross) < 1e-9) out.pop()
      else break
    }
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - p.x) < 1e-9 && Math.abs(last.y - p.y) < 1e-9) continue
    out.push(p)
  }
  return out
}

function isVertical(a: Pt, b: Pt): boolean { return Math.abs(a.x - b.x) < 1e-9 }
function isHorizontal(a: Pt, b: Pt): boolean { return Math.abs(a.y - b.y) < 1e-9 }

/** 每段都是水平或垂直，且都非零长 */
function wellFormed(pts: Pt[]): boolean {
  if (!pts || pts.length < 1) return false
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1]
    if (isVertical(a, b) && isHorizontal(a, b)) return false
    if (!isVertical(a, b) && !isHorizontal(a, b)) return false
  }
  return true
}

function collideH(lo: number, hi: number, y: number, r: Rect): boolean {
  return y >= r.y0 && y <= r.y1 && hi > r.x0 && lo < r.x1
}

function collideV(lo: number, hi: number, x: number, r: Rect): boolean {
  return x >= r.x0 && x <= r.x1 && hi > r.y0 && lo < r.y1
}

/** 整条折线是否都不碰外扩矩形（不含 source/target —— 它们已被排除） */
function polylineFree(pts: Pt[], rects: Rect[]): boolean {
  if (!wellFormed(pts)) return false
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1]
    const lo = isVertical(a, b) ? Math.min(a.y, b.y) : Math.min(a.x, b.x)
    const hi = isVertical(a, b) ? Math.max(a.y, b.y) : Math.max(a.x, b.x)
    if (!(hi - lo > EPS)) return false
    for (let k = 0; k < rects.length; k++) {
      const r = rects[k]
      if (isVertical(a, b) ? collideV(lo, hi, a.x, r) : collideH(lo, hi, a.y, r)) return false
    }
  }
  return true
}

/* --------------------------------------------------------- taken 错开 */

function collectTaken(taken: any): Pt[][] {
  const out: Pt[][] = []
  if (!Array.isArray(taken)) return out
  for (const t of taken) {
    const p = t && Array.isArray(t.points) ? t.points : null
    if (!p || p.length < 2) continue
    const clean: Pt[] = []
    for (const q of p) if (q && isFinite(q.x) && isFinite(q.y)) clean.push({ x: q.x, y: q.y })
    if (clean.length >= 2) out.push(clean)
  }
  return out
}

function orient(a: Pt, b: Pt, c: Pt): number {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
}

function onSeg(a: Pt, b: Pt, c: Pt): boolean {
  return Math.min(a.x, b.x) - EPS <= c.x && c.x <= Math.max(a.x, b.x) + EPS
    && Math.min(a.y, b.y) - EPS <= c.y && c.y <= Math.max(a.y, b.y) + EPS
}

function segsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const o1 = orient(a, b, c), o2 = orient(a, b, d)
  const o3 = orient(c, d, a), o4 = orient(c, d, b)
  if (((o1 > EPS && o2 < -EPS) || (o1 < -EPS && o2 > EPS))
    && ((o3 > EPS && o4 < -EPS) || (o3 < -EPS && o4 > EPS))) return true
  if (Math.abs(o1) <= EPS && onSeg(a, b, c)) return true
  if (Math.abs(o2) <= EPS && onSeg(a, b, d)) return true
  if (Math.abs(o3) <= EPS && onSeg(c, d, a)) return true
  if (Math.abs(o4) <= EPS && onSeg(c, d, b)) return true
  return false
}

function ptSegDist(p: Pt, a: Pt, b: Pt): number {
  const vx = b.x - a.x, vy = b.y - a.y
  const L2 = vx * vx + vy * vy
  if (L2 < 1e-18) return Math.hypot(p.x - a.x, p.y - a.y)
  let t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / L2
  if (t < 0) t = 0; else if (t > 1) t = 1
  return Math.hypot(p.x - (a.x + t * vx), p.y - (a.y + t * vy))
}

function segSegDist(a: Pt, b: Pt, c: Pt, d: Pt): number {
  if (segsCross(a, b, c, d)) return 0
  return Math.min(
    ptSegDist(a, c, d), ptSegDist(b, c, d),
    ptSegDist(c, a, b), ptSegDist(d, a, b),
  )
}

/** 已排连线摊平成"线段 + 包围盒"，一次算好、供所有候选复用（性能关键） */
interface SegFlat {
  ax: number; ay: number; bx: number; by: number
  x0: number; y0: number; x1: number; y1: number
  vert: boolean
}

function flattenTaken(taken: Pt[][]): SegFlat[] {
  const out: SegFlat[] = []
  for (const tk of taken) {
    for (let j = 0; j + 1 < tk.length; j++) {
      const a = tk[j], b = tk[j + 1]
      out.push({
        ax: a.x, ay: a.y, bx: b.x, by: b.y,
        x0: Math.min(a.x, b.x), x1: Math.max(a.x, b.x),
        y0: Math.min(a.y, b.y), y1: Math.max(a.y, b.y),
        vert: Math.abs(a.x - b.x) < 1e-9,
      })
    }
  }
  return out
}

/** 切比雪夫盒间距（欧氏距离的下界，用来快速扔掉远处的 taken 段） */
function boxGap(ax0: number, ay0: number, ax1: number, ay1: number, t: SegFlat): number {
  const dx = ax0 > t.x1 ? ax0 - t.x1 : (t.x0 > ax1 ? t.x0 - ax1 : 0)
  const dy = ay0 > t.y1 ? ay0 - t.y1 : (t.y0 > ay1 ? t.y0 - ay1 : 0)
  return dx > dy ? dx : dy
}

/** 候选段 vs 摊平段 t 的共线重叠长度 */
function segOverlap(a: Pt, b: Pt, t: SegFlat): number {
  const av = isVertical(a, b)
  if (av !== t.vert) return 0
  if (av) {
    if (Math.abs(a.x - t.ax) > 1e-9) return 0
    const lo = Math.max(Math.min(a.y, b.y), t.y0), hi = Math.min(Math.max(a.y, b.y), t.y1)
    return hi > lo ? hi - lo : 0
  }
  if (Math.abs(a.y - t.ay) > 1e-9) return 0
  const lo = Math.max(Math.min(a.x, b.x), t.x0), hi = Math.min(Math.max(a.x, b.x), t.x1)
  return hi > lo ? hi - lo : 0
}

/**
 * 打分：① 与 taken 的"叠线总长" ov（越小越好）；② 到 taken 的最小距离 cl
 *（越大越好，超过 capClear 一律按 capClear 记 —— 已经够远）。
 * **只看"身体"段**：端口相邻的首段/末段不计 —— 同一端口扇出的多条边必然共享那一段，
 * 拿它当错开依据只会把线越推越歪。
 * 剪枝：ov 一旦超过 bestOv 立刻放弃；距离用包围盒先筛掉远处的段 ⇒ 120 条边也快。
 */
function scoreOf(pts: Pt[], flat: SegFlat[], capClear: number, bestOv: number): { ov: number; cl: number } | null {
  const last = pts.length - 1
  const b0 = 1, b1 = last - 2                       // 身体段下标区间 [b0, b1]
  let ov = 0
  for (let i = b0; i <= b1; i++) {
    const a = pts[i], b = pts[i + 1]
    for (let k = 0; k < flat.length; k++) {
      const o = segOverlap(a, b, flat[k])
      if (o > 0) {
        ov += o
        if (ov > bestOv + 1e-9) return null          // 已经更差 ⇒ 不必再算
      }
    }
  }
  let cl = capClear
  for (let i = b0; i <= b1; i++) {
    const a = pts[i], b = pts[i + 1]
    const ax0 = Math.min(a.x, b.x), ax1 = Math.max(a.x, b.x)
    const ay0 = Math.min(a.y, b.y), ay1 = Math.max(a.y, b.y)
    for (let k = 0; k < flat.length; k++) {
      const t = flat[k]
      if (boxGap(ax0, ay0, ax1, ay1, t) >= cl) continue   // 连当前最小值都超不过
      const d = segSegDist(a, b, { x: t.ax, y: t.ay }, { x: t.bx, y: t.by })
      if (d < cl) cl = d
      if (cl <= 1e-9) return { ov, cl }                   // 已经贴上了 ⇒ 不必再算
    }
  }
  return { ov, cl }
}

/** 把若干竖直段的 x 平移 d；越界/反向则返回 null */
function shiftVerticals(pts: Pt[], segs: number[], d: number): Pt[] | null {
  const out: Pt[] = pts.map(p => ({ x: p.x, y: p.y }))
  for (const k of segs) {
    if (k <= 0 || k + 1 >= pts.length - 1) return null       // 端口所在段不许动
    const a = pts[k - 1], b = pts[k + 2]
    if (!a || !b) return null
    const lo = Math.min(a.x, b.x), hi = Math.max(a.x, b.x)
    const nx = pts[k].x + d
    if (!(nx > lo + EPS && nx < hi - EPS)) return null        // 不许越界/反向
    out[k].x = nx
    out[k + 1].x = nx
  }
  return out
}

/** 判定"够远"的阈值：间距 ≥ 它就不再徒劳微调（比它近才值得错开） */
const CLEAR_ENOUGH = 12

/**
 * 错开：在 ±10px（2px 步长）内微调竖直段 x，取**与 taken 最不叠**的那条。
 * 比较序：① 与 taken 的叠线总长更小；② 同分则到 taken 的最小距离更大（超过 CLEAR_ENOUGH 视为并列）。
 * 生成顺序固定（先整体平移、再逐段平移，d 从 ±2 递增），同分保留先生成者 ⇒ 完全确定性。
 * 基础路由若已"零叠线且够远"，直接返回（省掉无意义的最优化）。
 */
function nudge(pts: Pt[], rects: Rect[], flat: SegFlat[]): Pt[] {
  const verts: number[] = []
  for (let k = 0; k + 1 < pts.length; k++) if (isVertical(pts[k], pts[k + 1])) verts.push(k)
  const movable = verts.filter(k => k > 0 && k + 1 < pts.length - 1)
  if (!movable.length) return pts

  let best = pts
  const base = scoreOf(pts, flat, CLEAR_ENOUGH, Infinity)
  let bestOv = base ? base.ov : 0
  let bestCl = base ? base.cl : 0
  if (bestOv <= 1e-9 && bestCl >= CLEAR_ENOUGH - 1e-9) return pts

  const consider = (cand: Pt[] | null) => {
    if (!cand) return
    if (!wellFormed(cand) || !polylineFree(cand, rects)) return
    const sc = scoreOf(cand, flat, CLEAR_ENOUGH, bestOv)
    if (!sc) return
    if (sc.ov < bestOv - 1e-9 || (sc.ov < bestOv + 1e-9 && sc.cl > bestCl + 1e-9)) {
      best = cand; bestOv = sc.ov; bestCl = sc.cl
    }
  }
  for (const d of NUDGE) {
    if (d === 0) continue
    consider(shiftVerticals(pts, movable, d))                 // 所有竖直段整体平移
    for (const k of movable) consider(shiftVerticals(pts, [k], d))  // 逐段微调
  }
  return best
}
