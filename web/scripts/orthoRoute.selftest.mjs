#!/usr/bin/env node
/**
 * orthoRoute.selftest.mjs —— 纯 Node ESM 自测（零测试框架，只用 node 标准库）
 *
 * 跑法：
 *     node web/scripts/orthoRoute.selftest.mjs
 * 失败（任一断言不成立）以非零码退出。
 *
 * ---------------------------------------------------------------------------
 * 如何把 TS 编译出来再被 Node 跑（本脚本内部就是这么做的，命令已实测）：
 *
 *     cd web
 *     npx tsc src/orthoRoute.ts --outDir /tmp/or --module esnext --target es2020 --moduleResolution bundler
 *     # 产物：/tmp/or/orthoRoute.js（普通 ESM JS）
 *     # Node ≥ 22.7 会按语法自动识别 ESM（实测 Node v24.19.0 直接 import 即可）；
 *     # 老版本 Node 会把无 package.json 目录下的 .js 当 CJS ⇒ 需在 /tmp/or/ 放一个
 *     # package.json 内容 {"type":"module"}，或把产物改名 orthoRoute.mjs。
 *     # 本脚本自动补上那个 package.json，再 import 产物。
 *
 * 也可以直接跑源文件（Node ≥ 22.6 自带类型擦除，无需编译）：
 *     node some-test.mjs        # 内部 import '../src/orthoRoute.ts'
 * 本脚本优先走 tsc 编译产物（顺带证明 web/tsconfig 下能编译），tsc 不可用时回退到直接 import .ts。
 * ---------------------------------------------------------------------------
 */

import { spawnSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))       // web/scripts
const WEB = join(HERE, '..')                               // web
const TS_FILE = join(WEB, 'src', 'orthoRoute.ts')

/* ------------------------------------------------------------ 加载被测模块 */

async function loadRouter() {
  const outDir = mkdtempSync(join(tmpdir(), 'opennano-ortho-'))
  const tscBin = join(WEB, 'node_modules', 'typescript', 'bin', 'tsc')
  if (existsSync(tscBin)) {
    const cmd = [tscBin, TS_FILE, '--outDir', outDir, '--module', 'esnext',
      '--target', 'es2020', '--moduleResolution', 'bundler']
    const r = spawnSync(process.execPath, cmd, { encoding: 'utf8' })
    const js = join(outDir, 'orthoRoute.js')
    if (r.status === 0 && existsSync(js)) {
      writeFileSync(join(outDir, 'package.json'), '{"type":"module"}\n')
      console.log(`[setup] tsc 编译通过 → ${js}`)
      return { mod: await import(pathToFileURL(js).href), outDir }
    }
    console.log('[setup] tsc 编译失败，回退直接 import .ts')
    if (r.stdout) console.log(r.stdout)
    if (r.stderr) console.log(r.stderr)
  } else {
    console.log('[setup] 未找到 web/node_modules/typescript/bin/tsc，直接 import .ts（Node ≥ 22.6 类型擦除）')
  }
  return { mod: await import(pathToFileURL(TS_FILE).href), outDir }
}

const { mod: R, outDir } = await loadRouter()
const routeEdge = R.routeEdge

/* ------------------------------------------------------------------ 断言 */

let passed = 0
let failed = 0
const fails = []

function ok(cond, name, extra) {
  if (cond) { passed++; console.log('   ✓ ' + name) }
  else { failed++; fails.push(name + (extra ? ' — ' + extra : '')); console.log('   ✗ ' + name + (extra ? ' — ' + extra : '')) }
}
function section(t) { console.log('\n== ' + t + ' ==') }
const box = (x, y, w, h) => ({ x, y, w, h })
const round = (v) => Math.round(v * 1000) / 1000

/* --------------------------------------------------------------- 几何工具 */

function isOrtho(pts) {
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1]
    const h = Math.abs(a.y - b.y) < 1e-9
    const v = Math.abs(a.x - b.x) < 1e-9
    if (h === v) return false                       // 既非水平也非垂直 / 零长
  }
  return true
}
function turnsOf(pts) {
  let t = 0
  for (let i = 1; i + 1 < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], c = pts[i + 1]
    const d1 = Math.abs(a.y - b.y) < 1e-9 ? 'h' : 'v'
    const d2 = Math.abs(b.y - c.y) < 1e-9 ? 'h' : 'v'
    if (d1 !== d2) t++
  }
  return t
}
function noDupNoZero(pts) {
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1]
    if (Math.abs(a.x - b.x) < 1e-9 && Math.abs(a.y - b.y) < 1e-9) return false
  }
  return true
}
/** 外扩矩形 */
const inflate = (b, m) => ({ x0: b.x - m, y0: b.y - m, x1: b.x + b.w + m, y1: b.y + b.h + m })
const sameBox = (a, b) => a && b && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h
function segHitsRect(a, b, r) {
  if (Math.abs(a.y - b.y) < 1e-9) {                 // 水平
    const lo = Math.min(a.x, b.x), hi = Math.max(a.x, b.x)
    return a.y >= r.y0 - 1e-9 && a.y <= r.y1 + 1e-9 && hi > r.x0 + 1e-9 && lo < r.x1 - 1e-9
  }
  const lo = Math.min(a.y, b.y), hi = Math.max(a.y, b.y)
  return a.x >= r.x0 - 1e-9 && a.x <= r.x1 + 1e-9 && hi > r.y0 + 1e-9 && lo < r.y1 - 1e-9
}
/** 返回第一个撞外扩矩形的描述，或 null */
function findHit(pts, boxes, margin, skip) {
  const rects = boxes
    .filter(b => !skip.some(s => sameBox(b, s)))
    .map(b => inflate(b, margin))
  for (let i = 0; i + 1 < pts.length; i++) {
    for (let k = 0; k < rects.length; k++) {
      if (segHitsRect(pts[i], pts[i + 1], rects[k])) {
        return `段#${i} (${round(pts[i].x)},${round(pts[i].y)})→(${round(pts[i + 1].x)},${round(pts[i + 1].y)})`
          + ` 撞 外扩矩形[${round(rects[k].x0)},${round(rects[k].y0)}]-[${round(rects[k].x1)},${round(rects[k].y1)}]`
      }
    }
  }
  return null
}
/** 折线之间最小距离（两条线是否叠在一起） */
function ptSeg(p, a, b) {
  const vx = b.x - a.x, vy = b.y - a.y
  const L2 = vx * vx + vy * vy
  if (L2 < 1e-18) return Math.hypot(p.x - a.x, p.y - a.y)
  let t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / L2
  t = t < 0 ? 0 : t > 1 ? 1 : t
  return Math.hypot(p.x - (a.x + t * vx), p.y - (a.y + t * vy))
}
function segsCross(a, b, c, d) {
  const o = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
  const on = (p, q, r) => Math.min(p.x, q.x) - 1e-9 <= r.x && r.x <= Math.max(p.x, q.x) + 1e-9
    && Math.min(p.y, q.y) - 1e-9 <= r.y && r.y <= Math.max(p.y, q.y) + 1e-9
  const o1 = o(a, b, c), o2 = o(a, b, d), o3 = o(c, d, a), o4 = o(c, d, b)
  if (((o1 > 0 && o2 < 0) || (o1 < 0 && o2 > 0)) && ((o3 > 0 && o4 < 0) || (o3 < 0 && o4 > 0))) return true
  return (Math.abs(o1) < 1e-9 && on(a, b, c)) || (Math.abs(o2) < 1e-9 && on(a, b, d))
    || (Math.abs(o3) < 1e-9 && on(c, d, a)) || (Math.abs(o4) < 1e-9 && on(c, d, b))
}
function pathDist(p, q) {
  let best = Infinity
  for (let i = 0; i + 1 < p.length; i++) {
    for (let j = 0; j + 1 < q.length; j++) {
      const a = p[i], b = p[i + 1], c = q[j], d = q[j + 1]
      let v
      if (segsCross(a, b, c, d)) v = 0
      else v = Math.min(ptSeg(a, c, d), ptSeg(b, c, d), ptSeg(c, a, b), ptSeg(d, a, b))
      if (v < best) best = v
    }
  }
  return best
}
const vertXs = (pts) => {
  const out = []
  for (let i = 0; i + 1 < pts.length; i++) if (Math.abs(pts[i].x - pts[i + 1].x) < 1e-9) out.push(round(pts[i].x))
  return out
}
/** 两条线是否有「共线且正长度」的重合段（＝真正的叠线；垂直十字交叉不算） */
function overlaps(p, q, tol = 1e-9) {
  for (let i = 0; i + 1 < p.length; i++) {
    const a = p[i], b = p[i + 1]
    const av = Math.abs(a.x - b.x) < 1e-9
    for (let j = 0; j + 1 < q.length; j++) {
      const c = q[j], d = q[j + 1]
      const cv = Math.abs(c.x - d.x) < 1e-9
      if (av !== cv) continue
      if (av) {
        if (Math.abs(a.x - c.x) > tol) continue
        const lo = Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y))
        const hi = Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y))
        if (hi - lo > tol) return true
      } else {
        if (Math.abs(a.y - c.y) > tol) continue
        const lo = Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x))
        const hi = Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x))
        if (hi - lo > tol) return true
      }
    }
  }
  return false
}
/** 统一断言：端点 / 正交 / 无重复点 / 不撞外扩矩形 */
function checkRoute(name, pts, src, tgt, obstacles, margin, portOffset = 0) {
  const S = { x: src.x + src.w, y: src.y + src.h / 2 + portOffset }
  const T = { x: tgt.x, y: tgt.y + tgt.h / 2 + portOffset }
  ok(Array.isArray(pts) && pts.length >= 2, name + '：返回 ≥2 个点', pts && pts.length)
  if (!Array.isArray(pts) || !pts.length) return
  ok(Math.abs(pts[0].x - S.x) < 1e-9 && Math.abs(pts[0].y - S.y) < 1e-9, name + '：首点＝源端口',
    JSON.stringify(pts[0]) + ' vs ' + JSON.stringify(S))
  ok(Math.abs(pts[pts.length - 1].x - T.x) < 1e-9 && Math.abs(pts[pts.length - 1].y - T.y) < 1e-9,
    name + '：末点＝目标端口', JSON.stringify(pts[pts.length - 1]) + ' vs ' + JSON.stringify(T))
  ok(isOrtho(pts), name + '：全正交')
  ok(noDupNoZero(pts), name + '：无重复点/零长段')
  const hit = findHit(pts, obstacles, margin, [src, tgt])
  ok(hit === null, name + '：不撞任何外扩矩形', hit || '')
}

/* ============================================================ (a) 普通边 */

section('(a) 相邻列、中间没有障碍的普通边')
{
  const A = box(0, 0, 180, 80), B = box(260, 200, 180, 80)
  const obs = [A, B]
  const r = routeEdge({ source: A, target: B, obstacles: obs, taken: [] })
  checkRoute('(a)', r, A, B, obs, 6)
  ok(turnsOf(r) <= 2, '(a)：≤2 拐（优先「出源→缝里竖直走→进目标」）', 'turns=' + turnsOf(r))
  console.log('   路径:', r.map(p => `(${round(p.x)},${round(p.y)})`).join(' → '))

  // margin / portOffset 也要生效
  const r2 = routeEdge({ source: A, target: B, obstacles: obs, taken: [], margin: 20, portOffset: 12 })
  checkRoute('(a-margin20/port+12)', r2, A, B, obs, 20, 12)
}

/* ============================================================== (b) 扇出 */

section('(b) 一个源父节点往下挂 4 个子节点（扇出）')
{
  const parent = box(0, 300, 180, 80)
  const kids = [box(260, 0, 180, 80), box(260, 140, 180, 80), box(260, 280, 180, 80), box(260, 420, 180, 80)]
  const obs = [parent, ...kids]
  const taken = []
  for (let i = 0; i < kids.length; i++) {
    const r = routeEdge({ source: parent, target: kids[i], obstacles: obs, taken })
    checkRoute(`(b)-子${i}`, r, parent, kids[i], obs, 6)
    taken.push({ points: r })
  }
  // 四条边两两之间不许有「竖直段」重合（源端口处共享首段是必然的，故只查竖直段）
  const rs = taken.map(t => t.points)
  const vertOnly = (p) => {
    const out = []
    for (let i = 0; i + 1 < p.length; i++) if (Math.abs(p[i].x - p[i + 1].x) < 1e-9) out.push([p[i], p[i + 1]])
    return out
  }
  const vertOverlap = (p, q) => {
    for (const [a, b] of vertOnly(p)) for (const [c, d] of vertOnly(q)) {
      if (Math.abs(a.x - c.x) > 1e-9) continue
      const lo = Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y))
      const hi = Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y))
      if (hi - lo > 1e-9) return true
    }
    return false
  }
  for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) {
    ok(!vertOverlap(rs[i], rs[j]), `(b)：子${i} 与 子${j} 的竖直段不叠线`,
      JSON.stringify(vertXs(rs[i])) + ' vs ' + JSON.stringify(vertXs(rs[j])))
  }
  console.log('   竖直段 x：', rs.map(p => JSON.stringify(vertXs(p))).join(' | '))
}

/* ============================================================ (c) 反向向上 */

section('(c) 目标在源上方（反向）也要避障')
{
  const src = box(260, 400, 180, 80)
  const tgt = box(260, 0, 180, 80)
  const mid = box(260, 200, 180, 80)            // 正卡在源与目标之间
  const obs = [src, tgt, mid]
  const r = routeEdge({ source: src, target: tgt, obstacles: obs, taken: [] })
  checkRoute('(c)', r, src, tgt, obs, 6)
  const ys = r.map(p => p.y)
  ok(Math.min(...ys) <= 40 + 1e-9, '(c)：确实向上走到了目标高度', 'minY=' + round(Math.min(...ys)))
  ok(r.some(p => p.x > 440 + 1e-9), '(c)：绕开了中间障碍（走到了源列右侧）', 'maxX=' + round(Math.max(...r.map(p => p.x))))
  console.log('   路径:', r.map(p => `(${round(p.x)},${round(p.y)})`).join(' → '))
}

/* ==================================================== (d) 3 段路由会穿方块 */

section('(d) 直接 3 段路由会穿过障碍 ⇒ 必须绕开')
{
  const src = box(0, 0, 180, 80)
  const tgt = box(520, 0, 180, 80)
  const blocker = box(260, -40, 180, 160)       // 正好压住 y=40 的直线
  const obs = [src, tgt, blocker]
  const r = routeEdge({ source: src, target: tgt, obstacles: obs, taken: [] })
  checkRoute('(d)', r, src, tgt, obs, 6)
  ok(turnsOf(r) >= 4, '(d)：绕行至少 4 拐（直线被挡）', 'turns=' + turnsOf(r))
  ok(vertXs(r).length >= 2, '(d)：有绕行竖直段', JSON.stringify(vertXs(r)))
  console.log('   路径:', r.map(p => `(${round(p.x)},${round(p.y)})`).join(' → '))
}

/* ========================================================== (e) 平行边错开 */

section('(e) 两条平行边 ⇒ 竖直段必须错开')
{
  const A1 = box(0, 0, 180, 80), B1 = box(200, 300, 180, 80)
  const A2 = box(0, 140, 180, 80), B2 = box(200, 440, 180, 80)
  const obs = [A1, A2, B1, B2]
  const e1 = routeEdge({ source: A1, target: B1, obstacles: obs, taken: [] })
  checkRoute('(e)-边1', e1, A1, B1, obs, 6)
  const e2raw = routeEdge({ source: A2, target: B2, obstacles: obs, taken: [] })
  const e2 = routeEdge({ source: A2, target: B2, obstacles: obs, taken: [{ points: e1 }] })
  checkRoute('(e)-边2(带 taken)', e2, A2, B2, obs, 6)
  const v1 = vertXs(e1), v2 = vertXs(e2)
  ok(v1.length > 0 && v2.length > 0, '(e)：两条边都有竖直段', JSON.stringify([v1, v2]))
  ok(v1.every(x => !v2.includes(x)), '(e)：两条边的竖直段 x 不相同（错开了）',
    JSON.stringify(v1) + ' vs ' + JSON.stringify(v2))
  ok(!overlaps(e1, e2), '(e)：两条边没有重合段')
  ok(overlaps(e2raw, e1), '(e)：对照——不带 taken 时两条边确实会叠线（说明错开真的起了作用）',
    JSON.stringify(vertXs(e2raw)) + ' vs ' + JSON.stringify(v1))
  console.log('   边1:', e1.map(p => `(${round(p.x)},${round(p.y)})`).join(' → '))
  console.log('   边2:', e2.map(p => `(${round(p.x)},${round(p.y)})`).join(' → '))
  console.log('   边2(不带 taken，对照):', e2raw.map(p => `(${round(p.x)},${round(p.y)})`).join(' → '))
}

/* ========================================================== (f) 确定性 */

section('(f) 确定性：相同输入连调两次结果完全一致')
{
  const src = box(0, 0, 180, 80), tgt = box(520, 0, 180, 80), blocker = box(260, -40, 180, 160)
  const obs = [src, tgt, blocker]
  const t1 = routeEdge({ source: src, target: tgt, obstacles: obs, taken: [] })
  const taken = [{ points: t1 }]
  const p1 = routeEdge({ source: src, target: tgt, obstacles: obs, taken })
  const p2 = routeEdge({ source: src, target: tgt, obstacles: obs, taken })
  const p3 = routeEdge({ source: src, target: tgt, obstacles: obs, taken })
  ok(JSON.stringify(p1) === JSON.stringify(p2), '(f)：两次结果逐点相同', JSON.stringify(p1) + ' vs ' + JSON.stringify(p2))
  ok(JSON.stringify(p2) === JSON.stringify(p3), '(f)：第三次也相同')
  const q1 = routeEdge({ source: src, target: tgt, obstacles: obs, taken: [] })
  const q2 = routeEdge({ source: src, target: tgt, obstacles: obs, taken: [] })
  ok(JSON.stringify(q1) === JSON.stringify(q2), '(f)：无 taken 时也确定')
}

/* ================================================== (g) 压力：60 方块/120 边 */

section('(g) 压力测试：60 方块 + 120 边')
{
  const MARGIN = 6
  let seed = 20260913
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 }
  const COLS = 12, ROWS = 5, NW = 170, NH = 110, CX = 220, CY = 160
  const nodes = []
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const jx = Math.round((rnd() - 0.5) * 36)      // 列内抖动 ±18，最小净缝仍 ≥ 32
      const jy = Math.round((rnd() - 0.5) * 30)      // 行内抖动 ±15
      nodes.push(box(c * CX + jx, r * CY + jy, NW, NH))
    }
  }
  const edges = []
  for (let e = 0; e < 120; e++) {
    const a = Math.floor(rnd() * nodes.length)
    let b = Math.floor(rnd() * nodes.length)
    if (b === a) b = (b + 1) % nodes.length
    edges.push([a, b])
  }
  const taken = []
  let hits = 0
  const t0 = process.hrtime.bigint()
  for (let e = 0; e < edges.length; e++) {
    const [ai, bi] = edges[e]
    const src = nodes[ai], tgt = nodes[bi]
    const pts = routeEdge({ source: src, target: tgt, obstacles: nodes, taken, margin: MARGIN })
    const hit = findHit(pts, nodes, MARGIN, [src, tgt])
    if (hit) { hits++; if (hits <= 3) console.log('    撞了：edge' + e + ' ' + hit) }
    if (!isOrtho(pts) || !noDupNoZero(pts)) { hits++; console.log('    非正交/零长：edge' + e) }
    taken.push({ points: pts })
  }
  const ms = Number(process.hrtime.bigint() - t0) / 1e6
  ok(hits === 0, '(g)：120 条边全部不撞外扩矩形 / 全正交', 'hits=' + hits)
  ok(ms < 200, '(g)：总耗时 < 200ms', '实测 ' + round(ms) + 'ms')
  console.log(`   60 方块 / 120 边：${round(ms)}ms（平均 ${round(ms / 120)}ms/边）`)
}

/* ================================================ (h) 健壮性：绝不抛异常 */

section('(h) 健壮性：退化 / 异常输入绝不抛异常，端点仍然正确')
{
  const cases = [
    ['null 入参', null],
    ['undefined 入参', undefined],
    ['obstacles 为空', { source: box(0, 0, 100, 50), target: box(300, 200, 100, 50), obstacles: [], taken: [] }],
    ['taken 缺失', { source: box(0, 0, 100, 50), target: box(300, 200, 100, 50), obstacles: [box(0, 0, 100, 50), box(300, 200, 100, 50)] }],
    ['方块尺寸非法(NaN/负)', { source: box(0, 0, 100, 50), target: box(300, 0, 100, 50), obstacles: [{ x: 5, y: 5, w: -3, h: NaN }], taken: [] }],
    ['源＝目标同一矩形', { source: box(0, 0, 100, 50), target: box(0, 0, 100, 50), obstacles: [], taken: [] }],
    ['端口被邻块外扩压住（必走兜底）', { source: box(0, 0, 100, 100), target: box(400, 100, 100, 100), obstacles: [box(100, 0, 200, 100)], taken: [] }],
  ]
  for (const [name, arg] of cases) {
    let pts = null, err = null
    try { pts = routeEdge(arg) } catch (e) { err = e }
    ok(err === null, `(h) ${name}：不抛异常`, err && err.message)
    ok(Array.isArray(pts) && pts.length >= 1, `(h) ${name}：返回非空点列`, JSON.stringify(pts))
  }
  // 兜底形态：源端口 → 中点 x → 目标端口（此处无合法正交路，兜底允许穿方块）
  const fb = routeEdge({
    source: box(0, 0, 100, 100), target: box(400, 100, 100, 100),
    obstacles: [box(100, 0, 200, 100)], taken: [],
  })
  const want = [[100, 50], [250, 50], [250, 150], [400, 150]]
  ok(fb.length === want.length && want.every((w, i) => Math.abs(fb[i].x - w[0]) < 1e-9 && Math.abs(fb[i].y - w[1]) < 1e-9),
    '(h) 兜底＝「源端口→中点x→目标端口」3 段折线', JSON.stringify(fb.map(p => [p.x, p.y])))
}

/* ================================================================ 收尾 */

if (outDir) { try { rmSync(outDir, { recursive: true, force: true }) } catch { /* ignore */ } }
console.log('\n──────────────────────────────────────────────')
console.log(`通过 ${passed} 项，失败 ${failed} 项`)
if (failed) {
  console.log('失败清单：')
  for (const f of fails) console.log('  · ' + f)
  process.exit(1)
}
console.log('全部通过 ✅')
process.exit(0)
