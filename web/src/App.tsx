import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactFlow, {
  Background, Controls, Handle, MarkerType, Position, addEdge, SelectionMode,
  useNodesState, useEdgesState, Node, Edge, Connection,
} from 'reactflow'
import { api, download } from './api'
import Settings from './Settings'
import KbBrowser from './KbBrowser'
import BatchPanel from './BatchPanel'
import Dock from './Dock'
import ErrorBoundary from './ErrorBoundary'
import PanelTabs from './PanelTabs'
import type { Module, Library, CatalogItem, Equipment } from './types'

const KIND_COLOR: Record<string,string> = { process:'var(--kind-process)', inspect:'var(--kind-inspect)', design:'var(--kind-design)', sim:'var(--kind-sim)' }
// 工艺族配色(Linear 低饱和):光刻胶=琥珀, 曝光=雾蓝, 刻蚀=陶红, 沉积=青绿, 湿法=天青...
// 族色走 CSS 变量 → 主题(Linear / 高对比)切换时自动变
const FAMILY_COLOR: Record<string,string> = {
  resist:'var(--fam-resist)', expose:'var(--fam-expose)', etch:'var(--fam-etch)', dep:'var(--fam-dep)',
  wet:'var(--fam-wet)', dope:'var(--fam-dope)', bond:'var(--fam-bond)', pack:'var(--fam-pack)',
  thermal:'var(--fam-thermal)', assist:'var(--fam-assist)', metro:'var(--fam-metro)',
  metro_form:'var(--fam-metro-form)', metro_comp:'var(--fam-metro-comp)',
  metro_opt:'var(--fam-metro-opt)', metro_elec:'var(--fam-metro-elec)',
}

function ProcessNode({ data }: any) {
  const m: Module = data.module
  const isSeason = m.run_nature === 'season'
  const color = FAMILY_COLOR[m.family || ''] || KIND_COLOR[m.kind] || 'var(--faint)'
  const primary = m.equipment_name || m.name
  const secondary = m.family_label || m.subtype
  const rs = m.run_state || 'idle'
  const badge = m.disabled ? { t:'禁', c:'var(--faint)', bg:'transparent', bd:'var(--border)' }
    : isSeason       ? { t:'season', c:'var(--faint)', bg:'transparent', bd:'var(--border-2)' }
    : rs === 'running' ? { t:'…', c:'var(--accent-text)', bg:'var(--accent-soft)', bd:'var(--accent)' }
    : rs === 'ok'      ? { t:'✓', c:'var(--ok)', bg:'rgba(76,183,130,.14)', bd:'var(--ok)' }
    : rs === 'stale'   ? { t:'!', c:'var(--warn)', bg:'rgba(212,162,78,.14)', bd:'var(--warn)' }
    : { t:'○', c:'var(--faint)', bg:'transparent', bd:'var(--border)' }
  const kv = Object.entries(m.key_values || {}).slice(0, 3)
  /* —— 数据桥标识（每个模块都要能一眼看出"我是哪个 run"）——
     name/equipment_name 往往同型号重复（8 个 ICP 全叫 "ICP Etch"），没有 run 号就分不清谁是谁。
     短名只去批次前缀：`AR50-T1-ICP-0008` → `ICP-0008`（省地方，又保留工序+序号）。 */
  const runId = m.core_run_id || ''
  const batchId = m.core_batch_id || (runId.match(/^(.*)-[A-Za-z]+-\d{4}$/)?.[1] || '')
  const strip = (s: string) => (batchId && s.startsWith(batchId + '-') ? s.slice(batchId.length + 1) : s)
  const shortRun = runId ? strip(runId) : ''
  /* 本工序第几次：后端按 core 全量算（`stage_run_index`）；老工程没有这个字段时退回调参轮次 */
  const runNo = (m as any).stage_run_index ?? m.tune_step ?? null
  const shortSample = m.core_sample_id ? strip(m.core_sample_id) : ''
  return (
    <div className="proc-node" style={{ width:190, background:'var(--surface)', border:'1px solid var(--border)',
      borderLeft:`2px solid ${m.disabled || isSeason ? 'var(--faint)' : color}`, borderRadius:10,
      boxShadow:'var(--shadow-1)', color:'var(--text)', opacity: m.disabled ? .5 : (isSeason ? .68 : 1),
      borderStyle: m.disabled || isSeason ? 'dashed' : 'solid' }}>
      <Handle type="target" position={Position.Left} />
      <div style={{ padding:'6px 10px' }}>
        <div style={{ display:'flex', alignItems:'center', gap:6 }}>
          <span style={{ width:6, height:6, borderRadius:2, background:color, flexShrink:0 }} />
          <span style={{ fontSize: 'var(--fs-lg)', fontWeight:620, letterSpacing:'-.01em', flex:1, lineHeight:1.35,
            overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
            textDecoration: m.disabled ? 'line-through' : 'none' }}>{primary}</span>
          <span title={m.disabled ? '已禁用(不参与运行)'
                : isSeason ? 'season 热机（不加工已登记样品；默认收起，可在「视图」里展开）'
                : rs === 'ok' ? '已运行' : rs === 'stale' ? '上游已变,结果失效' : '未运行'}
            style={{ fontSize: 'var(--fs-micro)', lineHeight:'14px', minWidth:14, textAlign:'center',
              color:badge.c, background:badge.bg, border:`1px solid ${badge.bd}`, borderRadius:4,
              padding: isSeason ? '0 4px' : undefined }}>{badge.t}</span>
        </div>
        <div style={{ fontSize: 'var(--fs-sm)', color:'var(--muted)', marginTop:1, lineHeight:1.3 }}>{secondary}</div>
        {(shortRun || shortSample || runNo != null) && (
          <div style={{ marginTop:4, display:'flex', flexWrap:'wrap', gap:3 }}>
            {/* `run{N}` = **本工序第几次**（不含 season），由 core 全量算出来 ⇒ 序号不连续（0002/0003/0005/0006/0008）
                也一眼读得出"第 5 次"。以前只在"调参轮次"上显示，于是 ICP-0008 看着像第 8 次（owner 2026-09-13）。 */}
            {runNo != null && (
              <span title={`本工序第 ${runNo} 次（core run：${runId}）`
                + (m.tune_step != null ? `　·　参数调试线 ${m.tune_id || ''} 第 ${m.tune_step} 轮` : '')
                + '　序号不连续的是 core 的 run 号，这里按"第几次"显示'}
                style={{ fontSize: 'var(--fs-micro)', fontFamily:'var(--mono)', fontWeight:700,
                  color:'var(--accent-fg)', background:'var(--accent)',
                  border:'1px solid var(--accent)', borderRadius:4, padding:'0 5px' }}>
                run{runNo}
              </span>
            )}
            {shortRun && (
              <span title={`core run：${runId}`}
                style={{ fontSize: 'var(--fs-micro)', fontFamily:'var(--mono)', fontWeight:600,
                  color: m.tune_step != null ? 'var(--muted)' : 'var(--accent-hi)',
                  background: m.tune_step != null ? 'transparent' : 'var(--accent-soft)',
                  border: `1px solid ${m.tune_step != null ? 'var(--border)' : 'var(--accent-ring)'}`,
                  borderRadius:4, padding:'0 4px' }}>
                {shortRun}
              </span>
            )}
            {shortSample && (
              <span title={`样品/die：${m.core_sample_id}`}
                style={{ fontSize: 'var(--fs-micro)', fontFamily:'var(--mono)', color:'var(--text-2)',
                  background:'var(--raise)', border:'1px solid var(--border)',
                  borderRadius:4, padding:'0 4px' }}>
                {shortSample}
              </span>
            )}
            {m.core_stage_seq != null && (
              <span title={`工序序号 stage_seq=${m.core_stage_seq}`}
                style={{ fontSize: 'var(--fs-micro)', fontFamily:'var(--mono)', color:'var(--faint)',
                  border:'1px solid var(--border)', borderRadius:4, padding:'0 4px' }}>
                #{m.core_stage_seq}
              </span>
            )}
          </div>
        )}
        {kv.length > 0 && (
          <div style={{ marginTop:5, display:'flex', flexWrap:'wrap', gap:4 }}>
            {kv.map(([k, v]) => (
              <span key={k} style={{ fontSize: 'var(--fs-micro)', fontFamily:'var(--mono)', color:'var(--text-2)',
                background:'var(--raise)', border:'1px solid var(--border)', borderRadius:4, padding:'0 4px' }}>
                {k}={typeof v === 'number' ? Math.round(v * 1000) / 1000 : String(v)}
              </span>
            ))}
          </div>
        )}
      </div>
      {data.showComments && m.comment && (
        /* ⚠️ 备注收成**恒定的 1 行**（省略号；悬浮看全文）：
           以前限高 3 行仍会把节点撑高 ⇒ 画布整体变高、fitView 一缩，**字就变得很小**
           （2026-09-13 owner：「中间画布不够紧凑、字体缩得很小」）。1 行 = 高度恒定、布局可紧凑。 */
        <div title={m.comment}
          style={{ margin:'0 5px 5px', padding:'2px 6px', fontSize: 'var(--fs-xs)', lineHeight:'16px',
          color:'var(--text-2)', background:'rgba(212,162,78,.10)',
          border:'1px solid rgba(212,162,78,.35)', borderRadius:5,
          whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
          {m.comment}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
/** 左侧方阵里的**英文缩写**（一眼认得出是什么工序/检测）。
 *  表外条目回落成 subtype 大写前 6 位 —— 保证任何新增类别都有块可画。 */
const ABBR: Record<string, string> = {
  // 工艺族
  graphic: 'LITHO', etch: 'ETCH', deposition: 'DEP', doping: 'DOPE', bonding: 'BOND',
  packaging: 'PKG', wet: 'WET', thermal: 'THERM', assist: 'ASST',
  // 检测
  sem: 'SEM', tem: 'TEM', afm: 'AFM', xrd: 'XRD', xps: 'XPS', aes: 'AES', sims: 'SIMS',
  ellip: 'ELLIP', profilo: 'PROF', stress: 'STRESS', fourpp: '4PP', hall: 'HALL',
  cv: 'C-V', om: 'OM', fluor: 'FLUOR', ir: 'IR',
}
const abbrOf = (c: any) => ABBR[c.subtype] || String(c.subtype || c.name || '').slice(0, 6).toUpperCase()

/* 品牌标记（logo）：一个 N，由**三段工序**组成 —— 第一段点着强调色，后两段走 currentColor。
   与产品自身的视觉原子（节点方块 + 连线）同源；颜色全走 CSS 变量 ⇒ 三主题自动跟随。
   源文件（矢量的唯一真源）在 `web/public/brand/`。 */
function LogoMark({ size = 17 }: { size?: number }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <g fill="none" strokeLinecap="round" strokeLinejoin="round" strokeWidth={3.4}>
        <path d="M8.5 26 V6" stroke="var(--accent-text)" />
        <path d="M8.5 6 L23.5 26" stroke="currentColor" />
        <path d="M23.5 26 V6" stroke="currentColor" />
      </g>
    </svg>
  )
}

const nodeTypes = { process: ProcessNode }

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [catalog, setCatalog] = useState<CatalogItem[]>([])
  const [families, setFamilies] = useState<{key:string;label:string}[]>([])
  const [library, setLibrary] = useState<Library | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [kbTotal, setKbTotal] = useState(0)
  const [messages, setMessages] = useState<{role:string;content:string;src?:any[];tools?:any[]}[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [kbOpen, setKbOpen] = useState(false)
  const [projectName, setProjectName] = useState('未命名项目')
  const [loadOpen, setLoadOpen] = useState(false)
  const [projects, setProjects] = useState<{name:string;modules:number;edges:number;saved_at:string}[]>([])
  const importRef = useRef<HTMLInputElement>(null)
  const dataRef = useRef<HTMLInputElement>(null)
  const [edgeTip, setEdgeTip] = useState<{x:number;y:number;html:string}|null>(null)
  const [online, setOnline] = useState(false)
  const [resolvedRules, setResolvedRules] = useState<any[]>([])
  // BEAMER 式流程运行:运行状态/日志/问题/禁用/备注/右键菜单/视图
  const [running, setRunning] = useState(false)
  const [logs, setLogs] = useState<{t:string;kind:string;text:string}[]>([])
  const [issues, setIssues] = useState<{t:string;text:string}[]>([])
  const [dockTab, setDockTab] = useState<'agent'|'batch'|'log'|'issues'>('agent')
  const [fileMenu, setFileMenu] = useState(false)
  const [machDef, setMachDef] = useState<any>(null)
  /* 详情栏宽度：以后内容会越来越多 ⇒ 默认加宽（420）+ 左边缘可拖拽（记忆到 localStorage） */
  const [panelW, setPanelW] = useState<number>(() => {
    const v = Number(localStorage.getItem('opennano.panel.width'))
    return v >= 280 && v <= 760 ? v : 420
  })
  const panelDrag = useRef<{ x: number; w: number } | null>(null)      // 该机台的实测默认参数
  const [machPhase, setMachPhase] = useState('')          // 多段工艺时选哪一段
  const [showSeason, setShowSeason] = useState(false)
  const [libCollapsed, setLibCollapsed] = useState(false)
  const [menu, setMenu] = useState<{x:number;y:number;kind:'node'|'edge';id:string}|null>(null)
  const [viewMenu, setViewMenu] = useState(false)
  const [showComments, setShowComments] = useState(false)
  const [ortho, setOrtho] = useState(false)
  const [theme, setTheme] = useState<'linear'|'light'|'hc'>(
    () => (localStorage.getItem('opennano-theme') as 'linear'|'light'|'hc') || 'linear')
  const logRef = useRef<HTMLDivElement>(null)
  const saveRef = useRef<() => void>(() => {})
  const chatRef = useRef<HTMLDivElement>(null)
  const flowRef = useRef<any>(null)

  useEffect(() => {
    api.catalog().then(c => { setCatalog(c.module_catalog); setFamilies(c.families) })
    api.library().then(setLibrary)
    api.kbStats().then(s => setKbTotal(s.total)).catch(() => {})
    api.health().then(() => setOnline(true)).catch(() => setOnline(false))
    // 启动自动恢复最近编辑的项目(没有则保持空画布)
    api.loadProject().then(d => {
      if (d && Array.isArray(d.modules) && d.modules.length > 0) {
        loadProjectObj(d)
      }
    }).catch(() => {})
  }, [])

  useEffect(() => { chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior:'smooth' }) }, [messages])

  const onConnect = useCallback((c: Connection) =>
    setEdges(eds => addEdge({ ...c, ...EDGE_BASE }, eds)), [setEdges])

  // 靠近吸附自动连线:节点拖放后,若下方/附近有节点且端口距离近 → 自动连线(不用手画)
  const onNodeDragStop = useCallback((_: any, node: Node) => {
    const NW = 176, NH = 52   // 节点近似宽/高
    const cx = node.position.x + NW / 2
    const bottom = node.position.y + NH
    for (const other of nodes) {
      if (other.id === node.id) continue
      const ox = other.position.x + NW / 2
      const dx = Math.abs(cx - ox)
      const dy = other.position.y - bottom      // 本节点底部 → 对方顶部
      if (dy > -24 && dy < 60 && dx < 70) {
        setEdges(eds => {
          if (eds.some(e => e.source === node.id && e.target === other.id)) return eds
          return addEdge({ id: `e-${node.id}-${other.id}`, source: node.id,
            target: other.id, ...EDGE_BASE }, eds)
        })
        break
      }
    }
  }, [nodes, setEdges])

  const addModule = async (item: CatalogItem, pos?: {x:number;y:number}) => {
    const m = await api.newModule(item.subtype, pos?.x ?? (80 + nodes.length * 30), pos?.y ?? (80 + nodes.length * 30))
    setNodes(nds => [...nds, { id: m.id, type:'process', position:{x:m.x, y:m.y}, data:{ module:{ ...m, run_state:'idle' } } }])
    setSelectedId(m.id)
    pushLog('edit', `添加节点「${m.name}」`)
  }

  // BEAMER: 双击库项 → 插入到选中节点之后(自动改接线);无选中则普通添加
  const insertAfterSelected = async (item: CatalogItem) => {
    if (!selectedId) { await addModule(item); return }
    const out = edges.filter(e => e.source === selectedId)
    const src = nodes.find(n => n.id === selectedId)
    const pos = src ? { x: src.position.x + 30, y: src.position.y + 120 } : undefined
    const m = await api.newModule(item.subtype, pos?.x ?? 0, pos?.y ?? 0)
    setNodes(nds => [...nds, { id: m.id, type:'process', position:{x:m.x, y:m.y}, data:{ module:{ ...m, run_state:'idle' } } }])
    if (out.length === 1) {
      const target = out[0].target
      setEdges(eds => [
        ...eds.filter(e => e.id !== out[0].id),
        { id:`e-${selectedId}-${m.id}`, source:selectedId, target:m.id, ...EDGE_BASE },
        { id:`e-${m.id}-${target}`, source:m.id, target, ...EDGE_BASE },
      ])
      pushLog('edit', `插入节点「${m.name}」(位于选中节点之后,已自动改接线)`)
    } else {
      pushLog('edit', `添加节点「${m.name}」`)
    }
    setSelectedId(m.id)
  }

  const selectedNode: Node | undefined = nodes.find(n => n.id === selectedId)

  const pushLog = useCallback((kind: string, text: string) => {
    const t = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setLogs(ls => [...ls.slice(-299), { t, kind, text }])
  }, [])

  // 参数/接口变化 → 本节点及其下游结果失效(BEAMER: 改参数会 reset 后续模块)
  const invalidateDownstream = useCallback((fromId: string) => {
    const down = new Set<string>([fromId])
    let grew = true
    while (grew) {
      grew = false
      for (const e of edges) {
        if (down.has(e.source) && !down.has(e.target)) { down.add(e.target); grew = true }
      }
    }
    setNodes(nds => nds.map(n => down.has(n.id)
      ? { ...n, data: { ...n.data, module: { ...n.data.module, run_state: n.id === fromId ? 'stale' : (n.data.module.run_state === 'ok' ? 'stale' : n.data.module.run_state) } } }
      : n))
  }, [edges, setNodes])

  const updateModule = (patch: Partial<Module>) => {
    if (!selectedNode) return
    const m = { ...selectedNode.data.module, ...patch }
    setNodes(nds => nds.map(n => n.id === selectedId ? { ...n, data:{ module:m } } : n))
    const affects = ['params','key_values','formulas','param_inputs','param_outputs',
                     'material','equipment_id','machine_id','disabled']
    if (Object.keys(patch).some(k => affects.includes(k))) invalidateDownstream(selectedId!)
  }

  // ---- 流程运行(Run / Run To) ----
  const runFlow = async (until?: string) => {
    if (running || nodes.length === 0) return
    setRunning(true)
    const untilName = until ? (nodes.find(n => n.id === until)?.data.module.name || until) : null
    pushLog('run', untilName ? `运行到「${untilName}」` : `运行整个流程(${nodes.length} 个节点)`)
    setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, module: { ...n.data.module, run_state: 'running' } } })))
    try {
      const r = await api.flowRun({
        modules: nodes.map(n => n.data.module as Module),
        edges: edges.map(e => ({ src: e.source, dst: e.target, disabled: !!(e.data as any)?.disabled })),
        until: until || null,
      })
      setNodes(nds => nds.map(n => {
        const kv = r.results[n.id]
        if (!kv) return { ...n, data: { ...n.data, module: { ...n.data.module, run_state: n.data.module.disabled ? 'idle' : 'stale' } } }
        return { ...n, data: { ...n.data, module: { ...n.data.module, key_values: kv, run_state: 'ok' } } }
      }))
      for (const l of r.log) {
        if (l.status === 'ok') pushLog('ok', `${l.name} → ${JSON.stringify(l.outputs || {})}`)
        else pushLog(l.status === 'error' ? 'error' : 'warn', `${l.name}: ${l.reason || l.status}`)
      }
      if (r.errors?.length) {
        const t = new Date().toLocaleTimeString('zh-CN', { hour12: false })
        setIssues(is => [...is, ...r.errors.map((e: any) => ({ t, text: `${e.name}: ${e.error}` }))])
        setDockTab('issues')
      }
      if (r.cyclic?.length) pushLog('warn', `检测到环(未运行): ${r.cyclic.join(' → ')}`)
      pushLog('run', `完成:运行 ${r.ran} 个,跳过 ${r.skipped} 个,错误 ${r.errors?.length || 0} 个`)
    } catch (e: any) {
      pushLog('error', '运行失败: ' + e.message)
      const t = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      setIssues(is => [...is, { t, text: '运行失败: ' + e.message }])
      setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, module: { ...n.data.module, run_state: 'idle' } } })))
    } finally { setRunning(false) }
  }

  const patchNode = (id: string, patch: Partial<Module>) => {
    setNodes(nds => nds.map(n => n.id === id ? { ...n, data: { ...n.data, module: { ...n.data.module, ...patch } } } : n))
  }

  const duplicateNode = (id: string) => {
    const src = nodes.find(n => n.id === id)
    if (!src) return
    const m = src.data.module as Module
    const nid = `md-${Math.random().toString(36).slice(2, 10)}`
    setNodes(nds => [...nds, { id: nid, type: 'process',
      position: { x: src.position.x + 28, y: src.position.y + 28 },
      data: { module: { ...m, id: nid, name: m.name, key_values: {}, run_state: 'idle' } } }])
    pushLog('edit', `复制节点「${m.name}」`)
  }

  const toggleDisable = (id: string) => {
    const m = nodes.find(n => n.id === id)?.data.module as Module | undefined
    patchNode(id, { disabled: !m?.disabled })
    invalidateDownstream(id)
    pushLog('edit', `${m?.disabled ? '启用' : '禁用'}节点「${m?.name}」`)
  }

  const editComment = (id: string) => {
    const m = nodes.find(n => n.id === id)?.data.module as Module | undefined
    const c = prompt('节点备注(显示在节点下方,可用「视图 → 显示备注」开关)', m?.comment || '')
    if (c === null) return
    patchNode(id, { comment: c })
  }

  const toggleEdgeDisabled = (edgeId: string) => {
    setEdges(eds => eds.map(e => e.id === edgeId
      ? { ...e, data: { ...(e.data as any), disabled: !(e.data as any)?.disabled },
          style: { ...(e.style || {}), strokeDasharray: !(e.data as any)?.disabled ? '5 4' : undefined,
                   stroke: !(e.data as any)?.disabled ? 'var(--faint)' : undefined } }
      : e))
    pushLog('edit', `切换连线状态`)
  }

  // 上游承接参数:入边源节点的 key_values ∩ 本模块 inputs
  const handed = useMemo(() => {
    if (!selectedNode) return {}
    const m: Module = selectedNode.data.module
    const out: Record<string, number> = {}
    for (const e of edges) {
      if (e.target !== selectedNode.id) continue
      const src = nodes.find(n => n.id === e.source)
      const kv = src?.data?.module?.key_values || {}
      for (const k of m.param_inputs) if (kv[k] != null) out[k] = kv[k]
    }
    return out
  }, [edges, nodes, selectedNode])

  // ---- 膜层堆叠传递(镀膜结果 → 待曝光衬底结构) ----
  // 沿入边向上收集所有设了输出膜层的节点,按画布 y(上→下 = 先→后)排成堆叠
  const upstreamStack = useCallback((nodeId: string): { film: string; thickness: number }[] => {
    const films: { y: number; film: string; thickness: number }[] = []
    const seen = new Set<string>()
    const walk = (id: string) => {
      if (seen.has(id)) return
      seen.add(id)
      for (const e of edges) if (e.target === id) walk(e.source)
      if (id === nodeId) return
      const n = nodes.find(x => x.id === id)
      const mm = n?.data?.module as Module | undefined
      if (mm?.material?.film) films.push({ y: n!.position.y, film: mm.material.film,
        thickness: Number(mm.material.thickness) || 0 })
    }
    walk(nodeId)
    return films.sort((a, b) => a.y - b.y).map(({ film, thickness }) => ({ film, thickness }))
  }, [edges, nodes])

  // 某节点输出端的顶层膜:自身是沉积且设了材料 → 自己的膜;否则继承上游堆叠顶层;默认 Si 衬底
  const outputTopFilm = useCallback((nodeId: string): string => {
    const mm = nodes.find(x => x.id === nodeId)?.data?.module as Module | undefined
    if (mm?.family === 'dep' && mm.material?.film) return mm.material.film
    const s = upstreamStack(nodeId)
    return s.length ? s[s.length - 1].film : 'Si'
  }, [nodes, upstreamStack])

  // 选中节点/膜堆叠/工艺变化时,向后端解析当前上下文下生效的影响规则(定性+定量)
  const resolveSig = useMemo(() => {
    if (!selectedId) return ''
    const mm = nodes.find(x => x.id === selectedId)?.data?.module as Module | undefined
    return JSON.stringify([outputTopFilm(selectedId), mm?.equipment_name || mm?.name || '',
                           mm?.family || '', mm?.machine_name || ''])
  }, [selectedId, nodes, outputTopFilm])
  useEffect(() => {
    if (!selectedId) { setResolvedRules([]); return }
    const [surface, process, , machine] = JSON.parse(resolveSig)
    api.rulesResolve({ surface_film: surface, process, machine })
      .then(r => setResolvedRules(r.resolved)).catch(() => setResolvedRules([]))
  }, [selectedId, resolveSig])

  const compute = async () => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    const ctx = { surface_film: outputTopFilm(selectedNode.id),
                  process: m.equipment_name || m.name, machine: m.machine_name || '' }
    const r = await api.compute(m.params, handed, m.key_values, m.formulas, ctx)
    updateModule({ key_values: r.key_values })
  }

  // 当前选中的节点(框选/Shift 点选都进这里)
  const selectedNodes = useMemo(() => nodes.filter(n => n.selected), [nodes])
  const selectedEdgeCount = useMemo(() => edges.filter(e => e.selected).length, [edges])

  // 删除选中:框选的一批节点 + 单独选中的连线(兼容只点中一个节点的老行为)
  const deleteSelected = useCallback(() => {
    const ids = new Set(selectedNodes.map(n => n.id))
    if (selectedId) ids.add(selectedId)
    const eids = new Set(edges.filter(e => e.selected).map(e => e.id))
    if (ids.size === 0 && eids.size === 0) return
    setNodes(nds => nds.filter(n => !ids.has(n.id)))
    setEdges(eds => eds.filter(e => !ids.has(e.source) && !ids.has(e.target) && !eids.has(e.id)))
    setSelectedId(null)
    pushLog('edit', `删除 ${ids.size} 个节点 / ${eids.size} 条连线`)
  }, [selectedNodes, selectedId, edges, setNodes, setEdges])

  // 键盘快捷键(BEAMER 式):Ctrl+A 全选 / F3 备注 / Ctrl+0 适配 / Ctrl+± 缩放 / Ctrl+D 复制 / Ctrl+S 保存
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
                              || el.tagName === 'SELECT' || el.isContentEditable)
      const mod = e.metaKey || e.ctrlKey
      if (e.key === 'F3') { e.preventDefault(); setShowComments(v => !v); return }
      if (typing) return
      if (e.key === 'Escape') { setMenu(null); setViewMenu(false); return }
      if (mod && e.key.toLowerCase() === 'a') { e.preventDefault(); setNodes(nds => nds.map(n => ({ ...n, selected: true }))); return }
      if (mod && e.key === '0') { e.preventDefault(); fitMode('contain'); return }
      if (mod && (e.key === '=' || e.key === '+')) { e.preventDefault(); flowRef.current?.zoomIn(); return }
      if (mod && e.key === '-') { e.preventDefault(); flowRef.current?.zoomOut(); return }
      if (mod && e.key.toLowerCase() === 'd' && selectedId) { e.preventDefault(); duplicateNode(selectedId); return }
      if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); saveRef.current(); return }
      if (mod && e.key === 'Enter') { e.preventDefault(); runFlow(selectedId || undefined); return }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setNodes, selectedId, duplicateNode, runFlow])

  // 选中节点被删(键盘/按钮)后清空失效的 selectedId
  useEffect(() => {
    if (selectedId && !nodes.some(n => n.id === selectedId)) setSelectedId(null)
  }, [nodes, selectedId])

  // 主题:linear(默认) / hc(高对比度,PyCharm High Contrast 风格)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('opennano-theme', theme)
  }, [theme])

  // 正交连线(BEAMER: Manhattan Line Connection)
  useEffect(() => {
    setEdges(eds => eds.map(e => e.type === (ortho ? 'smoothstep' : 'default') ? e
      : { ...e, type: ortho ? 'smoothstep' : 'default' }))
  }, [ortho, setEdges])

  // 把当前节点的接口定义(承接/影响/公式)存回设备模板,后续节点继承
  const saveAsTemplate = async () => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    if (!m.equipment_id) return
    await api.eqUpdate(m.equipment_id, { inputs: m.param_inputs, outputs: m.param_outputs, formulas: m.formulas })
    alert('已存为设备模板：之后新拖入并选用该设备的节点将继承此接口定义。')
  }

  const onEquipment = async (eid: string) => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    const r = await api.applyEquipment(m.subtype, eid)
    updateModule({ equipment_id: eid, equipment_name: r.equipment_name,
      family: r.family, family_label: r.family_label,
      param_defs: r.param_defs, param_inputs: r.param_inputs,
      param_outputs: r.param_outputs, formulas: r.formulas,
      params: {}, param_meta: {} })
  }

  const onParam = (key: string, val: number) => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    updateModule({ params: { ...m.params, [key]: val } })
  }

  const onKeyValue = (key: string, val: number) => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    updateModule({ key_values: { ...m.key_values, [key]: val } })
  }

  const edgeTipHtml = (edgeId: string) => {
    const e = edges.find(x => x.id === edgeId)
    if (!e) return ''
    const src = nodes.find(n => n.id === e.source)?.data?.module as Module | undefined
    const dst = nodes.find(n => n.id === e.target)?.data?.module as Module | undefined
    if (!src || !dst) return ''
    const kv = src.key_values || {}
    const hand = dst.param_inputs.filter(k => kv[k] != null).map(k => `${k} = ${kv[k]}`)
    const lines = [`<b>${src.equipment_name || src.name} → ${dst.equipment_name || dst.name}</b>`,
                   `承接参数: ${hand.length ? hand.join(' · ') : '—'}`]
    if (src.material?.film) {
      const thk = Number(src.material.thickness) || 0
      lines.push(`输出膜层: ${src.material.film}${thk ? ` (${thk} nm)` : ''}`)
    }
    const top = outputTopFilm(e.source)
    const bias = library?.bias_table?.[top]
    if (bias != null) lines.push(`对下一步影响: GDS bias ${bias > 0 ? '+' : ''}${bias} nm`)
    return lines.join('<br/>')
  }

  const genGds = async () => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    const p = m.params || {}
    const bias = p.gds_bias ?? 0
    const lw = (p.size_nm ?? 500) + bias
    const r = await api.gds({ pitch_nm: p.pitch ?? 1000, linewidth_nm: lw, nx: 2, ny: 2, cell_um: 100, label: 'OpenNano' })
    alert('GDS 已生成:\n' + r.path + (bias ? `\n已应用 CD bias ${bias > 0 ? '+' : ''}${bias} nm → 写入线宽 ${lw} nm` : ''))
  }

  const genGdsLive = async () => {
    if (!selectedNode) return
    const m: Module = selectedNode.data.module
    const p = m.params || {}
    const bias = p.gds_bias ?? 0
    const lw = (p.size_nm ?? 500) + bias
    const r = await api.gdsLive({ pitch_nm: p.pitch ?? 1000, linewidth_nm: lw, nx: 2, ny: 2, cell_um: 100, label: 'OpenNano' })
    alert(r.ok ? '已绘制到 KLayout\n' + r.log + (bias ? `\n(应用 bias ${bias > 0 ? '+' : ''}${bias} nm → 线宽 ${lw} nm)` : '') : '绘制失败: ' + r.error)
  }

  const save = async () => {
    const name = (prompt('项目名称（同名将覆盖）:', projectName) ?? projectName).trim()
    if (!name) return
    const modules = nodes.map(n => n.data.module as Module)
    const es = edges.map(e => ({ src: e.source, dst: e.target }))
    const r = await api.saveProject(name, modules, es)
    setProjectName(name)
    alert(`已保存「${r.name}」（${r.modules} 个模块）`)
  }

  saveRef.current = save

  // ---- 项目载入(多项目) ----
  const openLoad = async () => {
    const r = await api.projectList().catch(() => ({ projects: [] }))
    setProjects(r.projects || [])
    setLoadOpen(true)
  }

  /* 画布连线分两类（**不许混淆**）：
       · recorded —— core 的 parent_run_id 明确写的，实线；
       · inferred —— run 没写 parent 时按**工艺顺序**补的显示边，虚线 + 标注。
     两者在导出/入库口径上都不等价：inferred 只是"看起来的顺序"，不是记录下来的归属。 */
  /* 边默认样式：**平滑曲线 + 箭头**（默认不用折角；正交折角在「视图」里可切） */
  const EDGE_BASE = { type: 'default' as const,
    markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 } }

  const edgeOf = (e: any) => {
    const inferred = (e._link || e.link) === 'inferred'
    return {
      id: `e-${e.src ?? e.src_module}-${e.dst ?? e.dst_module}`,
      source: String(e.src ?? e.src_module), target: String(e.dst ?? e.dst_module),
      ...EDGE_BASE,
      data: { inferred },
      /* 推断边：虚线 + 细箭头；**不挂文字标签**（一个点扇出 5 条时标签会挤成一团，
         顶栏已有「推断连线 N 条」图例说明含义） */
      style: inferred
        ? { strokeDasharray: '6 5', stroke: 'var(--faint)', strokeWidth: 1.2, opacity: .75 }
        : undefined,
    }
  }

  /** 套用「机台实测默认值」：把该段的值写进 params，并**补上缺失的参数行**（可在面板里改）。
   *  只读 core 推出来的值，来源 run 与日期都会写进日志 —— 不是手编的常量。 */
  const applyMachineDefaults = () => {
    if (!m || !machDef || !machPhase) return
    const blk = machDef.by_phase?.[machPhase]
    if (!blk) return
    const params = { ...(m.params || {}) }
    const defs = { ...(m.param_defs || {}) }
    for (const [k, rawV] of Object.entries<any>(blk.params || {})) {
      const v = (typeof rawV === 'number' ? rawV : 0)
      params[k] = v
      if (!defs[k]) {
        const meta: any = (blk.per_key || {})[k] || {}
        defs[k] = { label: k, unit: '', default: v,
                    min: Number(meta.min ?? 0), max: Number(meta.max ?? 0) }
      }
    }
    updateModule({ params, param_defs: defs })
    const src = blk.per_key ? Object.values(blk.per_key)[0] as any : null
    pushLog('edit', `套用机台实测值：${machDef.tool_id} · ${machPhase} 段（${Object.keys(blk.params).length} 个键`
      + (src?.from_run ? ` · 来源 ${src.from_run} ${src.date}` : '') + '）')
  }

  /* 详情栏左边缘拖拽调宽 */
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!panelDrag.current) return
      const w = Math.min(760, Math.max(280, panelDrag.current.w + (panelDrag.current.x - e.clientX)))
      setPanelW(w)
    }
    const up = () => {
      if (panelDrag.current) {
        localStorage.setItem('opennano.panel.width', String(panelW))
        panelDrag.current = null
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  }, [panelW])

  /** 一键自动整理：调后端**同一份**布局算法，只把新坐标写回画布（边/标注不动）。 */
  const arrangeLayout = async () => {
    if (nodes.length === 0) return
    try {
      const d = await api.arrangeLayout(nodes.map(n => n.data.module as Module),
                                        edges.map(e => ({ src: e.source, dst: e.target,
                                                          _link: (e.data as any)?.inferred ? 'inferred' : 'recorded' })))
      const pos = new Map(d.modules.map((m: any) => [m.id, { x: m.x, y: m.y }]))
      setNodes(ns => ns.map(n => {
        const p = pos.get(n.id)
        return p ? { ...n, position: { x: p.x, y: p.y } } : n
      }))
      setTimeout(() => fitMode('height'), 60)
      pushLog('view', `自动整理布局：${d.summary?.cols ?? '?'} 列 / ${d.summary?.rows ?? '?'} 行（只改位置）`)
    } catch (e: any) { pushLog('warn', '自动整理布局失败: ' + e.message) }
  }

  const loadProjectObj = (d: any) => {
    setNodes((d.modules || []).map((m: Module) => ({
      id: m.id, type: 'process', position: { x: m.x ?? 0, y: m.y ?? 0 }, data: { module: m },
    })))
    setEdges((d.edges || []).map(edgeOf))
    setProjectName(d.name || 'EXP')
    setSelectedId(null)
    setTimeout(() => fitMode('height'), 120)
  }

  const loadProjectByName = async (name: string) => {
    const d = await api.loadProject(name)
    setNodes((d.modules || []).map((m: Module) => ({
      id: m.id, type: 'process', position: { x: m.x ?? 0, y: m.y ?? 0 }, data: { module: m },
    })))
    setEdges((d.edges || []).map(edgeOf))
    setLoadOpen(false)
    loadProjectObj(d)
  }

  // ---- 配置导出/导入(设备库+参数+规则+知识库 一键打包) ----
  const exportConfig = async () => {
    const data = await api.configExport()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `opennano_config_${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const importConfig = async (file: File) => {
    try {
      const bundle = JSON.parse(await file.text())
      if (bundle.kind !== 'opennano-config') { alert('不是 OpenNano 配置文件'); return }
      const r = await api.configImport({ library: bundle.library, kb_entries: bundle.kb_entries })
      api.library().then(setLibrary)
      api.kbStats().then(s => setKbTotal(s.total)).catch(() => {})
      alert(`导入完成：\n配置库 ${r.library === 'replaced' ? '已替换（原文件自动备份）' : '未变'}\n知识库 +${r.kb_added} 新增 / ${r.kb_updated} 更新`)
    } catch (e: any) {
      alert('导入失败: ' + e.message)
    }
  }

  // ---- 数据导入(上传 Excel:先预检列/行,确认后入库) ----
  const importData = async (file: File) => {
    try {
      const buf = await file.arrayBuffer()
      const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)))
      const base = { filename: file.name, content_b64: b64, process_type: 'RIE_Cl' }
      const pre = await api.kbIngestUpload({ ...base, dry_run: true })
      const ok = confirm(
        `预检「${file.name}」\n\n参数列(${pre.params.length}): ${pre.params.slice(0, 8).join(', ')}${pre.params.length > 8 ? ' …' : ''}\n` +
        `结果列(${pre.results.length}): ${pre.results.slice(0, 10).join(', ')}${pre.results.length > 10 ? ' …' : ''}\n` +
        `共 ${pre.rows} 行,其中 ${pre.filled_rows} 行有结果数据\n\n确认导入知识库？`)
      if (!ok) return
      const r = await api.kbIngestUpload({ ...base, dry_run: false })
      api.kbStats().then(s => setKbTotal(s.total)).catch(() => {})
      alert(`导入完成：新增 ${r.added} 条 / 更新 ${r.updated} 条（共 ${r.entries} 条数据行）\n结果字段：${(r.result_keys || []).join(', ')}`)
    } catch (e: any) {
      alert('导入失败: ' + e.message)
    }
  }

  // ---- 实验数据包(文件夹⇄画布 双向桥梁) ----
  const exportExpack = async () => {
    try {
      const purpose = prompt('实验目的(写入 manifest,可空):', '') ?? ''
      const size = await download('/api/expack/export', {
        name: projectName, purpose, core_eq_state: (window as any).__dshEqState || [],
        modules: nodes.map(n => n.data.module as Module),
        edges: edges.map(e => ({ src: e.source, dst: e.target })),
      })
      pushLog('edit', `导出实验数据包「${projectName}」`)
      alert(`实验数据包已导出（${(size / 1024).toFixed(0)} KB zip）\n\n`
        + `结构：{项目名}/ 流程_{批次}.md + manifest + flow + batches/runs/steps/measurements(待填模板)/observations + artifacts/ + gds/\n`
        + `列名与数据域 core 逐列一致 → 实验后填数值+放 SEM 图 → 交《数据》会话 build_core 落库。\n`
        + `其中「流程_*.md」是给人看的流程卡（上机对照/交接），数据仍以 CSV 为准。`)
    } catch (e: any) { alert('导出失败: ' + e.message) }
  }

  // 画布流程 → 实验流程卡(Markdown,人读;与包内那张同一份)
  const exportCard = async () => {
    try {
      const purpose = prompt('实验目的(写入卡头,可空):', '') ?? ''
      const size = await download('/api/expack/card', {
        name: projectName, purpose,
        modules: nodes.map(n => n.data.module as Module),
        edges: edges.map(e => ({ src: e.source, dst: e.target })),
      })
      pushLog('edit', `导出实验流程卡「${projectName}」`)
      alert(`实验流程卡已导出（${(size / 1024).toFixed(1)} KB .md）\n\n`
        + `人读版：批次信息 + 设备链 + 逐步参数（中文标签/单位）+ 待填测量清单 + 上机检查项。\n`
        + `⚠️ 只读参考：要改流程请改画布后重新导出；实测值填在包的 measurements.csv / observations.csv。`)
    } catch (e: any) { alert('导出失败: ' + e.message) }
  }

  // 追加包：只含尚未入 core 的 run（镜像包 core-slice 会被整包跳过，必须走这个出口）
  const exportAppend = async () => {
    try {
      const proj = { project_name: projectName, modules: nodes.map(n => n.data.module as Module),
        edges: edges.map(e => ({ src: e.source, dst: e.target })),
        core_eq_state: (window as any).__dshEqState || [] }
      const pv = await (await fetch('/api/expack/append/preview', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(proj) })).json()
      if (!pv.count) { alert('没有需要追加的 run（core 里都已有）\n\n' + (pv.note || '')); return }
      const purpose = prompt(`将追加 ${pv.count} 个 run：\n${pv.new_runs.join('\n')}\n\n实验目的(可空):`, '') ?? ''
      const saveDir = prompt('落盘目录（便于交《数据》验收，可空=只下载）:', '~/Downloads/opennano_append') ?? ''
      const r = await fetch('/api/expack/append', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...proj, purpose, save_dir: saveDir }) })
      if (!r.ok) throw new Error(`${r.status}`)
      const info = r.headers.get('X-Append-Info') ? JSON.parse(decodeURIComponent(r.headers.get('X-Append-Info')!)) : {}
      const blob = await r.blob()
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
      a.download = `${info.batch_id || projectName}_append.zip`; a.click()
      pushLog('edit', `导出追加包「${projectName}」：${pv.new_runs.length} 个新 run`)
      const dates = Object.entries(info.date_source || {}).map(([k, v]: any) => `  ${k}: ${v}`).join('\n')
      const samples = Object.entries(info.sample_id_source || {}).map(([k, v]: any) => `  ${k}: ${v}`).join('\n')
      alert(`追加包已导出（${(blob.size / 1024).toFixed(1)} KB）\n\n`
        + `含 ${pv.new_runs.length} 个新 run：${pv.new_runs.join(', ')}\n`
        + (samples ? `\nsample_id 来源（继承 core，不凭空造号）:\n${samples}\n` : '')
        + (dates ? `\nrun 日期来源:\n${dates}\n` : '')
        + (info.saved_to ? `\n已落盘: ${info.saved_to}\n` : '')
        + `\nmanifest.source=tool-append ⇒ 不会被 core-slice 规则跳过；既有源优先，老行不会被覆盖。`)
    } catch (e: any) { alert('导出失败: ' + e.message) }
  }

  const importExpack = async () => {
    const path = prompt('实验数据包路径（文件夹或 zip，如 ~/Downloads/AR50-T1）：', '')
    if (!path || !path.trim()) return
    try {
      const d = await api.expackImport(path.trim())
      loadProjectObj(d)
      pushLog('run', `导入实验包「${d.name}」：${(d.modules || []).length} 节点 / ${(d.edges || []).length} 连线`)
    } catch (e: any) { alert('导入失败: ' + e.message) }
  }

  // ---- 数据导出(core 9 表 + 量名词 + 项目画布) ----
  const exportData = async () => {
    try {
      const proj = {
        name: projectName,
        modules: nodes.map(n => n.data.module as Module),
        edges: edges.map(e => ({ src: e.source, dst: e.target })),
      }
      const size = await download('/api/export/data', { project: proj })
      alert(`已导出 core 数据工作簿（${(size / 1024).toFixed(1)} KB）\n工作表: core 9 表全量 + 量名词 + 项目画布（权威源=core）`)
    } catch (e: any) {
      alert('导出失败: ' + e.message)
    }
  }

  // ---- Agent 画布操作执行器(前端为画布唯一真相,按序执行 op) ----
  const matchNode = (n: any, ref: string) => {
    const m = n.data?.module || {}
    const hay = [m.name, m.equipment_name, m.machine_name, m.subtype].filter(Boolean).join(' ').toLowerCase()
    const r = String(ref || '').toLowerCase().trim()
    if (!r) return false
    return hay.includes(r) || r === String(m.id)
  }

  const applyCanvasOps = async (ops: any[]) => {
    if (!ops?.length) return
    const nds = nodes.map(n => ({ ...n, data: { ...n.data, module: { ...n.data.module } } }))
    let eds = edges.map(e => ({ ...e }))
    const refMap: Record<string, string> = {}
    let doRun = false, until: string | undefined
    for (const op of ops) {
      if (op.type === 'add_module') {
        const m = await api.newModule(op.subtype, op.x ?? 90 + (nds.length % 3) * 60,
                                      op.y ?? 60 + nds.length * 130)
        if (op.name) m.name = op.name
        if (op.equipment_name) {
          const list = (library?.equipment || {})[m.subtype] || []
          const eq = list.find((e: any) => e.name === op.equipment_name || e.name.includes(op.equipment_name))
          if (eq) { const r2 = await api.applyEquipment(m.subtype, eq.id); Object.assign(m, r2, { equipment_id: eq.id, equipment_name: eq.name }) }
          else pushLog('warn', `画布: 未找到设备模板「${op.equipment_name}」`)
        }
        if (op.machine_name) {
          const mc = (library?.machines || []).find((x: any) => x.name === op.machine_name || x.tool_id === op.machine_name)
          if (mc) { m.machine_id = mc.id; m.machine_name = mc.name }
          else pushLog('warn', `画布: 未找到机台「${op.machine_name}」`)
        }
        if (op.params && Object.keys(op.params).length) m.params = { ...(m.params || {}), ...op.params }
        m.run_state = 'idle'
        nds.push({ id: m.id, type: 'process', position: { x: m.x, y: m.y }, data: { module: m } })
        if (op.ref) refMap[op.ref] = m.id
        pushLog('edit', `Agent 添加节点「${m.equipment_name || m.name}」`)
      } else if (op.type === 'connect') {
        const sid = refMap[op.src] || nds.find(n => matchNode(n, op.src))?.id
        const tid = refMap[op.dst] || nds.find(n => matchNode(n, op.dst))?.id
        if (sid && tid && sid !== tid) {
          eds.push({ id: `e-${sid}-${tid}`, source: sid, target: tid, ...EDGE_BASE })
          pushLog('edit', `Agent 连线 ${op.src} → ${op.dst}`)
        } else pushLog('warn', `Agent 连线失败(${op.src} → ${op.dst})`)
      } else if (op.type === 'set_params') {
        const n = nds.find(x => refMap[op.node] === x.id || matchNode(x, op.node))
        if (n) { n.data.module.params = { ...(n.data.module.params || {}), ...op.params }
                 pushLog('edit', `Agent 设参数 ${op.node}: ${JSON.stringify(op.params)}`) }
        else pushLog('warn', `Agent 设参数失败: 未找到「${op.node}」`)
      } else if (op.type === 'delete_nodes') {
        const ids = new Set(op.nodes.map((r: string) => refMap[r] || nds.find(n => matchNode(n, r))?.id).filter(Boolean))
        for (let i = nds.length - 1; i >= 0; i--) if (ids.has(nds[i].id)) nds.splice(i, 1)
        eds = eds.filter(e => !ids.has(e.source) && !ids.has(e.target))
        pushLog('edit', `Agent 删除 ${ids.size} 个节点`)
      } else if (op.type === 'clear') {
        nds.length = 0; eds = []; pushLog('edit', 'Agent 清空画布')
      } else if (op.type === 'run') {
        doRun = true; until = op.until ? (refMap[op.until] || op.until) : undefined
      }
    }
    setNodes(nds); setEdges(eds)
    setSelectedId(nds.length ? nds[nds.length - 1].id : null)
    if (doRun) setTimeout(() => runFlow(until), 400)
  }

  const send = async () => {
    const msg = input.trim()
    if (!msg || sending) return
    setSending(true)
    setInput('')
    const history = messages.map(m => ({ role: m.role, content: m.content }))
    setMessages(ms => [...ms, { role: 'user', content: msg }])
    try {
      const r = await api.agentChat(msg, history)
      setMessages(ms => [...ms, { role: 'assistant', content: r.answer, src: r.sources, tools: r.tool_calls }])
      if (r.canvas_ops?.length) {
        pushLog('run', `Agent 提交 ${r.canvas_ops.length} 条画布操作`)
        await applyCanvasOps(r.canvas_ops)
      }
    } catch (e: any) {
      setMessages(ms => [...ms, { role: 'assistant', content: '出错: ' + e.message }])
    } finally { setSending(false) }
  }

  /* 面板用的模块：**字段归一**。模块可能来自包/core/续做，形状不一定一致
     （2026-09-13：续做生成的模块缺 `key_values` ⇒ 面板 `m.key_values[k]` 抛错 ⇒ 整屏变白）。
     这里把结构性字段统一补成空值，语义不变（空就是空），但渲染永远安全。 */
  const rawM: Module | undefined = selectedNode?.data.module
  /* 机台实测默认参数：选中节点换了机台就去拉一次（只读 core） */
  useEffect(() => {
    const mid = rawM?.machine_id
    const nm = rawM?.machine_name
    const st = rawM?.core_stage || rawM?.subtype || ''
    if (!rawM || (!mid && !nm)) { setMachDef(null); return }
    let alive = true
    api.machineDefaults(/drie/i.test(String(st)) ? 'DRIE' : '').then(d => {
      if (!alive || !d?.available) { setMachDef(null); return }
      const g = (d.groups || []).find((x: any) => x.machine_id && x.machine_id === mid)
        || (d.groups || []).find((x: any) => x.tool_id === nm || x.model === nm)
        || null
      setMachDef(g)
      setMachPhase(g?.phases?.length === 1 ? g.phases[0] : '')
    }).catch(() => setMachDef(null))
    return () => { alive = false }
  }, [rawM?.id, rawM?.machine_id, rawM?.machine_name])

  const m: Module | undefined = rawM && {
    ...rawM,
    params: rawM.params || {}, param_defs: rawM.param_defs || {},
    param_inputs: rawM.param_inputs || [], param_outputs: rawM.param_outputs || [],
    formulas: rawM.formulas || {}, key_values: rawM.key_values || {},
    material: rawM.material || {}, annotations: rawM.annotations || [],
  }
  const catEquipment: Equipment[] = m && library ? library.equipment[m.subtype] || [] : []
  // 入射膜堆叠(曝光类节点 Process Link 用)
  const inStack = m ? upstreamStack(m.id) : []
  /* season（热机）节点：数据保留、默认不画（owner 2026-09-12 裁断）。
     过滤只作用于"渲染"，绝不动 nodes/edges 本体（保存/导出仍是全量）。 */
  const seasonIds = useMemo(() => new Set(
    nodes.filter(n => (n.data.module as Module).run_nature === 'season').map(n => n.id)), [nodes])
  const viewNodes = useMemo(
    () => (showSeason ? nodes : nodes.filter(n => !seasonIds.has(n.id))), [nodes, seasonIds, showSeason])
  const viewEdges = useMemo(
    () => (showSeason ? edges : edges.filter(e => !seasonIds.has(e.source) && !seasonIds.has(e.target))),
    [edges, seasonIds, showSeason])
  const inferredEdgeCount = useMemo(
    () => viewEdges.filter(e => (e.data as any)?.inferred).length, [viewEdges])
  /** 画布适配的**四种标准模式**（owner 2026-09-13 点名的那套术语）：
   *
   *  · `contain` 等比适应 —— 整图缩进视口，可能留白（原来只有这一种）；
   *  · `cover`  双向铺满 —— 两个方向都填满，超出的部分靠平移（不留白）；
   *  · `width`  宽度适应 —— 横向填满，纵向超出（可上下平移）；
   *  · `height` 高度适应 —— 纵向填满，横向超出（可左右平移）—— 工艺是左右流动，这个最常看；
   *
   *  为什么要自己算：React Flow 的 `fitView` 只有 contain 一种，而且把缩放硬钳在
   *  `[minZoom, maxZoom]` 里 —— "铺满/单向适应"必须自己求缩放与平移，再用 `setViewport` 落下去。
   *  支点：**内容包围盒**（只算当前可见节点，season 收起时不参与）。
   */
  const fitMode = useCallback((mode: 'contain' | 'cover' | 'width' | 'height' | '1') => {
    const inst = flowRef.current
    const box = document.querySelector('.canvas-wrap') as HTMLElement | null
    if (!inst || !box) return
    const ns = (inst.getNodes?.() || []) as any[]
    const vis = new Set(viewNodes.map(n => n.id))
    const pts = ns.filter(n => vis.has(n.id))
    if (!pts.length) return
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (const n of pts) {
      const w = n.width || n.measured?.width || 190
      const h = n.height || n.measured?.height || 71
      x0 = Math.min(x0, n.position.x); y0 = Math.min(y0, n.position.y)
      x1 = Math.max(x1, n.position.x + w); y1 = Math.max(y1, n.position.y + h)
    }
    const W = box.clientWidth, H = box.clientHeight
    const pad = 22
    const sx = (W - pad * 2) / Math.max(1, x1 - x0)
    const sy = (H - pad * 2) / Math.max(1, y1 - y0)
    let z = mode === 'cover' ? Math.max(sx, sy)
      : mode === 'width' ? sx
        : mode === 'height' ? sy
          : Math.min(sx, sy)
    z = Math.max(0.2, Math.min(z, 1.6))
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2
    /* 定位：等比/铺满居中；单向适应把"另一方向"对齐到起点，方便顺着工艺方向平移 */
    const tx = mode === 'width' ? pad - x0 * z : W / 2 - cx * z
    const ty = mode === 'height' ? pad - y0 * z : H / 2 - cy * z
    inst.setViewport({ x: tx, y: ty, zoom: mode === '1' ? 1 : z })
  }, [viewNodes])

  const topFilmName = inStack.length ? inStack[inStack.length - 1].film : 'Si'
  const stackDesc = ['Si', ...inStack.map(l => l.film + (l.thickness ? ` (${l.thickness} nm)` : ''))].join(' / ')

  return (
    <ErrorBoundary label="主界面">
    <div className="app">
      <div className="topbar">
        <span className="brand" title="OpenNano · 工艺协同优化平台">
          <LogoMark />
          <span className="brand-name">OpenNano</span>
        </span>
        <span style={{ color:'var(--muted)' }}>· {projectName} · 组织记忆 {kbTotal} 条</span>
        <span style={{ display:'inline-flex', alignItems:'center', gap:5, fontSize: 'var(--fs-xs)', color:'var(--muted)' }}>
          <span style={{ width:8, height:8, borderRadius:'50%', background: online ? 'var(--ok)' : 'var(--bad)' }} />
          {online ? '后端已连接' : '后端未连接'}
        </span>
        {inferredEdgeCount > 0 && (
          <span title="虚线是**按工艺顺序**补的显示连线（core 里没有记录 parent）；实线才是 core 记录的真实上游"
            style={{ fontSize: 'var(--fs-xs)', color:'var(--muted)', border:'1px solid var(--line)',
                     borderRadius:10, padding:'1px 7px' }}>
            <svg width="26" height="8" style={{ verticalAlign:'middle', marginRight:4 }}>
              <line x1="0" y1="4" x2="26" y2="4" stroke="var(--faint)" strokeWidth="2"
                strokeDasharray="6 5" />
            </svg>
            推断连线 {inferredEdgeCount} 条（core 未记录）
          </span>
        )}
        <span className="spacer" />
        {/* 运行组(BEAMER: Run / Run To) */}
        <button className="btn" onClick={() => runFlow(selectedId || undefined)}
          disabled={running || nodes.length === 0}
          title={selectedId ? '运行到选中节点为止 (Run To)' : '运行整个流程 (Run)'}>
          {running ? '运行中…' : (selectedId ? '▶ 运行到此处' : '▶ 运行流程')}
        </button>
        <button className="btn ghost" onClick={() => runFlow()} disabled={running || !selectedId || nodes.length === 0}
          title="运行整个流程">全部运行</button>
        <span className="topbar-sep" />
        {/* 视图菜单 */}
        <div style={{ position:'relative' }}>
          <button className="btn ghost" onClick={() => setViewMenu(v => !v)} title="视图">视图 ▾</button>
          {viewMenu && (
            <div className="dropdown" onMouseLeave={() => setViewMenu(false)}>
              <label><input type="checkbox" checked={showComments}
                onChange={e => { setShowComments(e.target.checked); pushLog('view', `备注显示: ${e.target.checked ? '开' : '关'}`) }} /> 显示备注 (F3)</label>
              <label><input type="checkbox" checked={ortho}
                onChange={e => setOrtho(e.target.checked)} /> 折角连线(正交·默认用平滑曲线)</label>
              <label><input type="checkbox" checked={showSeason}
                onChange={e => setShowSeason(e.target.checked)}
                disabled={seasonIds.size === 0} /> 显示 season 节点{seasonIds.size ? ` (${seasonIds.size})` : ''}</label>
              <label><input type="checkbox" checked={libCollapsed}
                onChange={e => setLibCollapsed(e.target.checked)} /> 收起左侧工艺库</label>
              <div className="dropdown-sep" />
              <button className="dropdown-item" onClick={() => { fitMode('contain'); setViewMenu(false) }}>适配视图 · 等比适应 (Ctrl+0)</button>
              <button className="dropdown-item" onClick={() => { fitMode('cover'); setViewMenu(false) }}>适配视图 · 双向铺满</button>
              <button className="dropdown-item" onClick={() => { fitMode('width'); setViewMenu(false) }}>适配视图 · 宽度适应</button>
              <button className="dropdown-item" onClick={() => { fitMode('height'); setViewMenu(false) }}>适配视图 · 高度适应</button>
              <button className="dropdown-item" onClick={() => { fitMode('1'); setViewMenu(false) }}>原始比例 100%</button>
              <button className="dropdown-item" onClick={() => { setViewMenu(false); arrangeLayout() }}
                title="按工序列重排所有节点（只改位置，不动连线与标注）—— 方块叠在一起时一键复位">自动整理布局</button>
              <button className="dropdown-item" onClick={() => { setDockTab('log'); setViewMenu(false) }}>显示日志面板</button>
              <button className="dropdown-item" onClick={() => { setDockTab('issues'); setViewMenu(false) }}>显示问题面板 ({issues.length})</button>
              <div className="dropdown-sep" />
              <div style={{ padding:'4px 9px 2px', fontSize: 'var(--fs-micro)', letterSpacing:'.06em',
                textTransform:'uppercase', color:'var(--faint)', fontWeight:600 }}>主题</div>
              <button className="dropdown-item" onClick={() => setTheme('linear')}>
                {theme === 'linear' ? '● ' : '○ '}Linear(默认低饱和)</button>
              <button className="dropdown-item" onClick={() => setTheme('light')}>
                {theme === 'light' ? '● ' : '○ '}明亮工作台(浅色)</button>
              <button className="dropdown-item" onClick={() => setTheme('hc')}>
                {theme === 'hc' ? '● ' : '○ '}高对比度(PyCharm 风)</button>
              <div className="dropdown-sep" />
              <button className="dropdown-item" onClick={() => { setLogs([]); setViewMenu(false) }}>清空日志</button>
              <button className="dropdown-item" onClick={() => { setIssues([]); setViewMenu(false) }}>清空问题</button>
            </div>
          )}
        </div>
        <span className="topbar-sep" />
        <button className="btn ghost" onClick={() => setKbOpen(true)}>知识库</button>
        <button className="btn ghost" onClick={() => setSettingsOpen(true)}>设置</button>
        <button className="btn ghost" onClick={openLoad}>载入</button>
        <button className="btn ghost" onClick={save}>保存</button>
        <span className="topbar-sep" />
        <button className="btn ghost" onClick={() => setDockTab('batch')}
          title="批次管理：run 链 / 续做 / 表单填写 / 调试线 / DRIE 菜单直读">批次</button>
        {/* 文件（保存/载入/导入/导出全部收纳；owner 2026-09-12："好多种保存输出"⇒ 一个菜单） */}
        <div style={{ position:'relative' }}>
          <button className="btn ghost" onClick={() => setFileMenu(v => !v)} title="保存 / 载入 / 导入 / 导出">文件 ▾</button>
          {fileMenu && (
            <div className="dropdown" onMouseLeave={() => setFileMenu(false)}>
              {/* 按**功能**分组（不按"导入/导出"分）——方向由条目里的动词表达（owner 2026-09-13） */}
              <div className="dd-sec">项目</div>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); openLoad() }}>载入项目…</button>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); save() }}>保存项目</button>
              <div className="dropdown-sep" />
              <div className="dd-sec">实验包与流程卡<span className="dd-hint">与 core 对接</span></div>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); exportExpack() }}
                title="画布流程 → 实验数据包(core 格式,含人读流程卡.md)">导出实验包</button>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); exportCard() }}
                title="画布流程 → 实验流程卡(Markdown,人读,可打印上机)">导出流程卡</button>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); exportAppend() }}
                title="只导出尚未入 core 的 run（tool-append 包）">导出追加包（增量）</button>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); importExpack() }}
                title="实验数据包(文件夹/zip) → 画布流程">导入实验包…</button>
              <div className="dropdown-sep" />
              <div className="dd-sec">测量数据<span className="dd-hint">Excel 宽表</span></div>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); exportData() }}
                title="导出 core 数据工作簿(9表+量名词)">导出数据工作簿</button>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); dataRef.current?.click() }}
                title="上传 Excel 解析为 core 草稿(不落库)">导入数据…</button>
              <div className="dropdown-sep" />
              <div className="dd-sec">系统配置<span className="dd-hint">换机 / 备份</span></div>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); exportConfig() }}
                title="导出设备库/参数/影响规则/知识库">导出配置包</button>
              <button className="dropdown-item" onClick={() => { setFileMenu(false); importRef.current?.click() }}
                title="导入配置包(换机/备份)">导入配置包…</button>
            </div>
          )}
        </div>
        <input ref={dataRef} type="file" accept=".xlsx,.xlsm" style={{ display:'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) importData(f); e.target.value = '' }} />
        <input ref={importRef} type="file" accept=".json,application/json" style={{ display: 'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) importConfig(f); e.target.value = '' }} />
      </div>
      <div className="main">
        {libCollapsed && (
          <div className="sidebar collapsed" title="展开工艺库" onClick={() => setLibCollapsed(false)}>»</div>
        )}
        <div className="sidebar" style={libCollapsed ? { display: 'none' } : undefined}>
          {/* 形式统一：拖进去的**长方块**与画布上生成的方块同形（左边色条 + 圆角 + 渐变 + 流光） */}
          <h3>PROCESS<span className="dim">工艺</span></h3>
          <div className="lib-grid">
            {catalog.filter(c => c.group==='PROCESS').map(c => (
              /* 2026-09-13 owner：去掉中文小字、缩写居中；方块按**工艺族**上色（与画布节点同色）。 */
              <div key={c.subtype} className="lib-tile" draggable
                style={{ ['--tile-accent' as any]: FAMILY_COLOR[c.family || ''] || KIND_COLOR[c.kind] }}
                title={`${c.name}${c.family_label ? ' · ' + c.family_label : ''} · 单击添加到画布 · 双击插到选中节点之后 · 也可直接拖入`}
                onClick={() => addModule(c)}
                onDoubleClick={() => insertAfterSelected(c)}
                onDragStart={e => e.dataTransfer.setData('application/opennano', c.subtype)}>
                <span className="abbr">{abbrOf(c)}</span>
              </div>
            ))}
          </div>
          <h3>METROLOGY<span className="dim">检测</span></h3>
          <div className="lib-grid">
            {catalog.filter(c => c.group==='METROLOGY').map(c => (
              <div key={c.subtype} className="lib-tile" draggable
                style={{ ['--tile-accent' as any]: FAMILY_COLOR[c.family || ''] || KIND_COLOR[c.kind] }}
                title={`${c.name}${c.family_label ? ' · ' + c.family_label : ''} · 单击添加到画布 · 双击插到选中节点之后 · 也可直接拖入`}
                onClick={() => addModule(c)}
                onDoubleClick={() => insertAfterSelected(c)}
                onDragStart={e => e.dataTransfer.setData('application/opennano', c.subtype)}>
                <span className="abbr">{abbrOf(c)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="center-col">
        <div className="canvas-wrap">
          <ReactFlow nodes={viewNodes} edges={viewEdges} nodeTypes={nodeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect} onNodeDragStop={onNodeDragStop}
            deleteKeyCode={['Backspace', 'Delete']}
            selectionOnDrag
            selectionMode={SelectionMode.Partial}
            panOnDrag={[1, 2]}
            multiSelectionKeyCode={['Meta', 'Control', 'Shift']}
            onNodeClick={(_, n) => { setSelectedId(n.id); setMenu(null) }}
            onPaneClick={() => setMenu(null)}
            onNodeContextMenu={(e, n) => { e.preventDefault(); setSelectedId(n.id); setMenu({ x: e.clientX, y: e.clientY, kind: 'node', id: n.id }) }}
            onEdgeContextMenu={(e, edge) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, kind: 'edge', id: edge.id }) }}
            onEdgeClick={(_, edge) => setSelectedId(null)}
            onEdgeMouseEnter={(e, edge) => setEdgeTip({ x: e.clientX, y: e.clientY, html: edgeTipHtml(edge.id) })}
            onEdgeMouseMove={(e, edge) => setEdgeTip(t => t ? { ...t, x: e.clientX, y: e.clientY } : t)}
            onEdgeMouseLeave={() => setEdgeTip(null)}
            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }}
            onDrop={e => {
              e.preventDefault()
              const sub = e.dataTransfer.getData('application/opennano')
              if (!sub) return
              const item = catalog.find(c => c.subtype === sub)
              if (!item) return
              const pos = flowRef.current?.screenToFlowPosition({ x: e.clientX, y: e.clientY })
              addModule(item, { x: pos.x - 88, y: pos.y - 26 })
            }}
            /* 不再用 React Flow 自带的 fitView：它只有 contain 一种，还会把缩放钳在
               fitViewOptions 里 —— 四种适配模式统一走 `fitMode()`（载入时用**高度适应**）。 */
            minZoom={0.2} maxZoom={2} proOptions={{ hideAttribution: true }}
            onInit={inst => { flowRef.current = inst }}>
            <Background color={theme === 'light' ? 'rgba(15,23,42,.10)' : theme === 'hc' ? 'rgba(255,255,255,.10)' : 'rgba(255,255,255,.05)'} gap={22} />
            <Controls />
            {families.length > 0 && (
              <div style={{ position:'absolute', right:12, bottom:12, zIndex:5,
                display:'flex', alignItems:'center', gap:7,
                background:'color-mix(in srgb, var(--panel) 88%, transparent)',
                border:'1px solid var(--border)', borderRadius:999,
                padding:'5px 11px', backdropFilter:'blur(6px)' }}>
                <span style={{ fontSize: 'var(--fs-micro)', letterSpacing:'.06em', textTransform:'uppercase',
                  color:'var(--faint)', fontWeight:600 }}>族</span>
                {families.map(f => (
                  <span key={f.key} title={f.label}
                    style={{ width:9, height:9, borderRadius:3, cursor:'default',
                      background: FAMILY_COLOR[f.key] || 'var(--faint)',
                      transition:'transform .12s ease' }}
                    onMouseEnter={e => (e.currentTarget.style.transform = 'scale(1.35)')}
                    onMouseLeave={e => (e.currentTarget.style.transform = 'scale(1)')} />
                ))}
              </div>
            )}
            {(selectedNodes.length > 0 || selectedEdgeCount > 0) && (
              <div style={{ position:'absolute', top:10, left:'50%', transform:'translateX(-50%)', zIndex:10,
                background:'var(--panel)', border:'1px solid var(--accent)', borderRadius:10,
                padding:'6px 12px', display:'flex', alignItems:'center', gap:10, fontSize: 'var(--fs-base)',
                boxShadow:'var(--shadow-2)' }}>
                <span>已选 <b>{selectedNodes.length}</b> 个节点{selectedEdgeCount ? ` · ${selectedEdgeCount} 条连线` : ''}</span>
                <button className="btn" style={{ padding:'3px 12px', fontSize: 'var(--fs-base)' }} onClick={deleteSelected}>🗑 删除选中</button>
                <span style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)' }}>或按 Backspace</span>
              </div>
            )}
            {nodes.length === 0 && (
              <div style={{ position:'absolute', inset:0, display:'flex', alignItems:'center', justifyContent:'center', pointerEvents:'none' }}>
                <div style={{ color:'var(--muted)', fontSize: 'var(--fs-lg)', textAlign:'center', lineHeight:1.8 }}>从左侧拖入工艺开始构建流程<br/><span style={{fontSize: 'var(--fs-base)'}}>空白处按住鼠标拖拽 = 框选一批节点 · 中键/右键拖拽 = 平移画布 · Backspace 删除选中</span></div>
              </div>
            )}
          </ReactFlow>
        </div>
        <Dock
        active={dockTab}
        onTab={k => setDockTab(k as any)}
        tabs={[
          { key: 'agent', label: 'Agent 对话', render: () => (
            <div className="dock-col">
              <div className="chatbar-body" ref={chatRef}>
                {messages.length === 0 && <div className="chat-empty">问我工艺问题，例如「Ta 怎么刻蚀？」「SiO₂ 掩膜刻蚀常用参数」</div>}
                {messages.map((m, i) => (
                  <div key={i} className={"msg " + m.role}>
                    {m.tools && m.tools.length > 0 && (
                      <div className="msg-tools">{m.tools.map((t: any, j: number) => (
                        <div key={j}>{t.ok ? '🔧' : '⚠️'} {t.name}
                          {t.args && Object.keys(t.args).length > 0 && <span className="tool-args"> {JSON.stringify(t.args)}</span>}
                        </div>
                      ))}</div>
                    )}
                    <div className="msg-bubble">{m.content}</div>
                    {m.src && m.src.length > 0 && (
                      <div className="msg-src">{m.src.map((s: any, j: number) => <div key={j}>📎 [{s.reliability_score}/5] {s.title}</div>)}</div>
                    )}
                  </div>
                ))}
              </div>
              <div className="chatbar-input">
                <input value={input} onChange={e => setInput(e.target.value)}
                  onKeyDown={e => e.key==='Enter' && send()} placeholder="输入工艺问题…" />
                <button className="btn" onClick={send} disabled={sending}>{sending ? '…' : '发送'}</button>
              </div>
            </div>
          )},
          { key: 'batch', label: '批次', render: () => (
            <BatchPanel onClose={() => setDockTab('agent')} ctx={{
              projectName, modules: nodes.map(n => n.data.module as Module),
              edges: edges.map(e => ({ src: e.source, dst: e.target })),
              onApply: (p, log) => { loadProjectObj(p); pushLog('run', `批次续做：${log}`) },
              onFormChange: (modules, eqState) => {
                setNodes(ns => ns.map(n => {
                  const m = modules.find((x: any) => x.id === n.id)
                  return m ? { ...n, data: { ...n.data, module: m as Module } } : n
                }))
                if (eqState) (window as any).__dshEqState = eqState
              },
            }} />
          )},
          { key: 'log', label: `日志 (${logs.length})`, render: () => (
            <div className="log-body" ref={logRef}>
              {logs.length === 0 && <div className="chat-empty">运行流程后,这里逐步记录:承接参数 → 公式/规则 → 输出。</div>}
              {logs.map((l, i) => (
                <div key={i} className={'logline ' + l.kind}><span className="lt">{l.t}</span><span>{l.text}</span></div>
              ))}
            </div>
          )},
          { key: 'issues', label: `问题 (${issues.length})`, render: () => (
            <div className="log-body">
              {issues.length === 0 && <div className="chat-empty">暂无问题。</div>}
              {issues.map((x, i) => (
                <div key={i} className="logline error"><span className="lt">{x.t}</span><span>{x.text}</span></div>
              ))}
            </div>
          )},
        ]} />
        </div>
        <div className="panel-drag" title="拖拽调整详情栏宽度（双击复位 420）"
          onMouseDown={e => {
            panelDrag.current = { x: e.clientX, w: panelW }
            document.body.style.cursor = 'col-resize'
            document.body.style.userSelect = 'none'
          }}
          onDoubleClick={() => { setPanelW(420); localStorage.setItem('opennano.panel.width', '420') }} />
        {/* 详情栏上色（2026-09-13）：整栏跟随**选中节点的工艺族色** —— 与画布方块、左栏方块同一套色，
            一眼对上「这个节点属于哪一族」；没选中节点时不染色。 */}
        <div className={`panel${m ? ' has-fam' : ''}`}
          style={{ width: panelW, ['--fam' as any]: m ? (FAMILY_COLOR[m.family || ''] || KIND_COLOR[m.kind] || 'var(--faint)') : undefined } as any}>
          <ErrorBoundary label="节点面板" onReset={() => setSelectedId(null)}>
          {!m && <div style={{ color:'var(--muted)' }}>点击画布节点查看详情<br/>（左侧点工艺添加到画布，节点上下端口拖线连接）</div>}
          {m && (
            <>
              <div className="panel-head">
                <div className="ph-top">
                  <h2 style={{ margin:0 }}>{m.name}</h2>
                  <button className="btn ghost" onClick={deleteSelected} style={{ fontSize: 'var(--fs-base)', padding:'4px 10px' }}>🗑 删除选中</button>
                </div>
                <div className="ph-meta">
                  <span className="fam-dot" />
                  <span className="fam-label">{m.family_label || m.family || m.kind}</span>
                  <span className="dim">{m.kind} · {m.subtype}</span>
                </div>
              </div>
              <div className="card">
                <h4>工艺模板 / 机台</h4>
                <select value={m.equipment_id} onChange={e => onEquipment(e.target.value)}>
                  <option value="">— 内置默认 —</option>
                  {catEquipment.map(eq => <option key={eq.id} value={eq.id}>{eq.name}</option>)}
                </select>
                <div className="row" style={{ marginTop:6 }}>
                  <select value={m.machine_id || ''} onChange={e => {
                    const mc = (library?.machines || []).find((x: any) => x.id === e.target.value)
                    updateModule({ machine_id: e.target.value, machine_name: mc?.name || '' })
                  }}>
                    <option value="">— 机台（可选，同型号多台请分开选）—</option>
                    {(library?.machines || [])
                      .filter((mc: any) => !mc.equipment_id || !m.equipment_id || mc.equipment_id === m.equipment_id)
                      .map((mc: any) => (
                        <option key={mc.id} value={mc.id}>
                          {mc.name}{mc.model ? ` · ${mc.model}` : ''}{mc.serial ? ` #${mc.serial}` : ''}
                        </option>
                      ))}
                  </select>
                </div>
                {/* 机台实测默认值（只读 core 推出来的；按段分开） */}
                {machDef && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed var(--border)' }}>
                    <div className="bd-sec-head">
                      <span className="dim">实测默认值</span>
                      <span className="dim">{machDef.tool_id} · {machDef.n_runs} 次
                        {machDef.as_of ? ` · 最近 ${machDef.as_of}` : ''}</span>
                    </div>
                    <div className="row" style={{ gap: 6 }}>
                      {machDef.phases?.length > 1 && (
                        <select value={machPhase} onChange={e => setMachPhase(e.target.value)}
                          title="该机台是**多段工艺**（chuck/etch/dechuck…）⇒ 选一段套用；完整步序列在 steps 里">
                          <option value="">— 选一段 —</option>
                          {machDef.phases.map((p: string) => <option key={p} value={p}>{p}</option>)}
                        </select>
                      )}
                      <button className="btn ghost" disabled={!machPhase} onClick={applyMachineDefaults}
                        title="把该段实测值写进本节点参数（并补上缺失的参数行）；不覆盖你没动过的其它键之外的东西">
                        套用实测值{machPhase ? `（${machPhase}）` : ''}
                      </button>
                    </div>
                    {machDef.phases?.length === 1 && (
                      <div className="dim" style={{ fontSize: 'var(--fs-xs)', marginTop: 2 }}>
                        {Object.keys(machDef.by_phase[machDef.phases[0]]?.params || {}).length} 个键
                        （来源 {machDef.from_run}）
                      </div>
                    )}
                  </div>
                )}
              </div>
              {m.family === 'dep' && (
                <div className="card">
                  <h4>沉积材料（本步输出膜层）</h4>
                  <div className="row"><label>材料</label>
                    <input list="film-options" value={m.material?.film || ''} placeholder="如 SiO₂"
                      onChange={e => updateModule({ material: { ...(m.material || {}), film: e.target.value } })} />
                  </div>
                  <div className="row"><label>厚度 nm</label>
                    <input type="number" value={m.material?.thickness ?? 0}
                      onChange={e => updateModule({ material: { ...(m.material || {}), thickness: parseFloat(e.target.value) || 0 } })} />
                  </div>
                  <datalist id="film-options">
                    {Object.keys(library?.bias_table || {}).map(f => <option key={f} value={f} />)}
                  </datalist>
                </div>
              )}
              {m.family === 'expose' && (
                <div className="card">
                  <h4>Process Link（入射膜堆叠 · 生效影响规则）</h4>
                  <div style={{ fontSize: 'var(--fs-base)', color:'var(--muted)', marginBottom:8 }}>入射膜堆: {stackDesc}</div>
                  {resolvedRules.length === 0 && (
                    <div style={{ fontSize: 'var(--fs-base)', color:'var(--muted)' }}>当前上下文（film “{topFilmName}”）无生效影响规则（可在 设置 → 影响规则 中定义）</div>
                  )}
                  {resolvedRules.map((r: any) => (
                    <div key={r.id} style={{ borderBottom:'1px dashed var(--border)', paddingBottom:6, marginBottom:6 }}>
                      <div className="row">
                        <span className="chip">{r.from}</span>
                        <span style={{ color:'var(--accent-text)' }}>{r.sign || '→'}</span>
                        <span className="chip">{r.to}</span>
                        {r.value != null ? (
                          <>
                            <span style={{ fontWeight:700 }}>{r.value}</span>
                            <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }}
                              onClick={() => onParam(r.to, r.value)}>Apply</button>
                          </>
                        ) : (
                          <span style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)' }}>定性</span>
                        )}
                        <span style={{ marginLeft:'auto', fontSize: 'var(--fs-micro)', color:'var(--muted)' }}>[{r.reliability_score}/5]</span>
                      </div>
                      {(r.mechanism || r.expr) && (
                        <div style={{ fontSize: 'var(--fs-xs)', color:'var(--muted)' }}>
                          {r.mechanism}{r.expr ? ` · ${r.expr}` : ''}{r.source ? ` · ${r.source}` : ''}
                        </div>
                      )}
                    </div>
                  ))}
                  {m.params.gds_bias != null && (
                    <div className="row">
                      <label>gds_bias nm</label>
                      <input type="number" value={m.params.gds_bias}
                        onChange={e => onParam('gds_bias', parseFloat(e.target.value) || 0)} />
                      <span className="chip-x" title="清除"
                        onClick={() => { const p = { ...m.params }; delete p.gds_bias; updateModule({ params: p }) }}>✕</span>
                    </div>
                  )}
                </div>
              )}
              {m.equipment_name === 'GDS Layout' && (
                <div className="card">
                  <h4>版图生成（P4 · klayout）</h4>
                  <div className="row">
                    <button className="btn" onClick={genGds}>生成 GDS</button>
                    <button className="btn ghost" onClick={genGdsLive}>绘制到 KLayout</button>
                  </div>
                </div>
              )}
              <div className="card">
                <h4 style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                  Process Interface（承接 ← / 影响 →）
                  {m.equipment_id ? (
                    <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 8px' }} onClick={saveAsTemplate}>存为设备模板</button>
                  ) : (
                    <span style={{ fontSize: 'var(--fs-micro)', color:'var(--muted)', fontWeight:400 }}>选设备后可存模板</span>
                  )}
                </h4>
                <div className="iface-sec">承接 inputs（← 上游传入）</div>
                {m.param_inputs.map(k => (
                  <div className="row" key={k}>
                    <span className="chip">{k}</span>
                    <span className="kv-in">{handed[k] != null ? handed[k] : '—'}</span>
                    <span className="chip-x" onClick={() => updateModule({ param_inputs: m.param_inputs.filter(x => x !== k) })}>✕</span>
                  </div>
                ))}
                <div className="row">
                  <select value="" onChange={e => { if (e.target.value) updateModule({ param_inputs: [...m.param_inputs, e.target.value] }) }}>
                    <option value="">+ 添加承接参数…</option>
                    {Object.keys(library?.params || {}).filter(p => !m.param_inputs.includes(p)).map(p => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
                <div className="iface-sec">影响 outputs（→ 传给下游）</div>
                {m.param_outputs.map(k => (
                  <div className="row" key={k}>
                    <span className="chip">{k}</span>
                    <input type="number" value={m.key_values[k] ?? 0}
                      onChange={e => onKeyValue(k, parseFloat(e.target.value) || 0)} />
                    <span className="chip-x" onClick={() => updateModule({ param_outputs: m.param_outputs.filter(x => x !== k) })}>✕</span>
                  </div>
                ))}
                <div className="row">
                  <select value="" onChange={e => { if (e.target.value) updateModule({ param_outputs: [...m.param_outputs, e.target.value] }) }}>
                    <option value="">+ 添加影响参数…</option>
                    {Object.keys(library?.params || {}).filter(p => !m.param_outputs.includes(p)).map(p => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
                <div className="iface-sec">公式 formulas（输出 = 表达式）</div>
                {Object.entries(m.formulas).map(([out, expr]) => (
                  <div className="row" key={out}>
                    <span className="chip" style={{ flexShrink:0 }}>{out} =</span>
                    <input type="text" value={expr} placeholder="如 胶CD - 2 * bias_nm"
                      onChange={e => updateModule({ formulas: { ...m.formulas, [out]: e.target.value } })} />
                    <span className="chip-x" onClick={() => { const f = { ...m.formulas }; delete f[out]; updateModule({ formulas: f }) }}>✕</span>
                  </div>
                ))}
                <div className="row">
                  <select value="" onChange={e => { if (e.target.value) updateModule({ formulas: { ...m.formulas, [e.target.value]: '' } }) }}>
                    <option value="">+ 添加公式…</option>
                    {m.param_outputs.filter(o => !(o in m.formulas)).map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                  {Object.keys(m.formulas).length > 0 && <button className="btn" onClick={compute}>Compute</button>}
                </div>
              </div>
              <div className="card">
                <PanelTabs module={m} onUpdate={updateModule} />
              </div>
            </>
          )}
          </ErrorBoundary>
        </div>
      </div>

      {/* 状态栏(BEAMER 式:项目/规模/选中/运行/后端) */}
      <div className="statusbar">
        <span className="sb-name" title={`项目 ${projectName}`}>项目 {projectName}</span><span className="sb-sep" />
        <span>{nodes.length} 节点 · {edges.length} 连线</span><span className="sb-sep" />
        <span>选中 {selectedNode ? (selectedNode.data.module as Module).name : '—'}</span><span className="sb-sep" />
        <span>运行 {running ? '进行中…' : (nodes.some(n => (n.data.module as Module).run_state === 'ok') ? '已完成' : '待运行')}</span>
        <span className="spacer" />
        {nodes.some(n => (n.data.module as Module).disabled) && (
          <span>{nodes.filter(n => (n.data.module as Module).disabled).length} 个已禁用</span>
        )}
        <span style={{ color: online ? 'var(--ok)' : 'var(--bad)' }}>{online ? '后端已连接' : '后端未连接'}</span>
      </div>
      {kbOpen && <KbBrowser onClose={() => setKbOpen(false)} />}
      {loadOpen && (
        <div style={{ position:'fixed', inset:0, background:'rgba(8,9,10,.72)', backdropFilter:'blur(2px)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:2000 }}>
          <div style={{ width:520, background:'var(--panel)', border:'1px solid var(--border)', borderRadius:14, overflow:'hidden' }}>
            <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between' }}>
              <b>载入项目（{projects.length}）</b>
              <span style={{ cursor:'pointer', color:'var(--muted)' }} onClick={() => setLoadOpen(false)}>✕</span>
            </div>
            <div style={{ maxHeight:360, overflowY:'auto', padding:10 }}>
              {projects.map(p => (
                <div key={p.name} className="row" style={{ padding:'8px 10px', border:'1px solid var(--border)', borderRadius:10, marginBottom:6 }}>
                  <span style={{ flex:1, fontWeight:600 }}>{p.name}
                    <span style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)', fontWeight:400 }}> · {p.modules} 模块 / {p.edges} 连线 · {p.saved_at?.replace('T', ' ')}</span></span>
                  <button className="btn" style={{ fontSize: 'var(--fs-base)', padding:'4px 12px' }} onClick={() => loadProjectByName(p.name)}>载入</button>
                  <span className="chip-x" title="删除" onClick={async () => { await api.projectDelete(p.name); setProjects(ps => ps.filter(x => x.name !== p.name)) }}>✕</span>
                </div>
              ))}
              {projects.length === 0 && <div style={{ color:'var(--muted)', padding:12 }}>（还没有保存的项目——先点「保存」）</div>}
            </div>
          </div>
        </div>
      )}
      {settingsOpen && <Settings onClose={() => { setSettingsOpen(false); api.library().then(setLibrary) }} />}
      {menu && (() => {
        const nd = menu.kind === 'node' ? nodes.find(n => n.id === menu.id) : null
        const m = nd?.data.module as Module | undefined
        return (
          <div className="ctxmenu" style={{ left: menu.x, top: menu.y }}>
            <div className="ctx-title">{menu.kind === 'node' ? (m?.name || '节点') : '连线'}</div>
            {menu.kind === 'node' && m && (
              <>
                <button onClick={() => { setMenu(null); runFlow(menu.id) }}>▶ 运行到此处 (Run To)</button>
                <button onClick={() => { setMenu(null); runFlow() }}>▶▶ 运行整个流程</button>
                <button onClick={() => { setMenu(null); compute() }}>∑ 单独计算本节点</button>
                <div className="dropdown-sep" />
                <button onClick={() => { toggleDisable(menu.id); setMenu(null) }}>{m.disabled ? '启用节点' : '禁用节点'}</button>
                <button onClick={() => {
                  invalidateDownstream(menu.id)
                  setNodes(nds => {
                    const down = new Set<string>([menu.id]); let grew = true
                    while (grew) { grew = false; for (const e of edges) if (down.has(e.source) && !down.has(e.target)) { down.add(e.target); grew = true } }
                    return nds.map(n => down.has(n.id) ? { ...n, data: { ...n.data, module: { ...n.data.module, run_state: 'idle' } } } : n)
                  })
                  pushLog('edit', `重置「${m.name}」及其下游状态`)
                  setMenu(null)
                }}>重置本节点及下游</button>
                <button onClick={() => { editComment(menu.id); setMenu(null) }}>编辑备注…</button>
                <button onClick={() => { duplicateNode(menu.id); setMenu(null) }}>复制节点 (Ctrl+D)</button>
                <div className="dropdown-sep" />
                <button onClick={() => { setSelectedId(menu.id); setDockTab('log'); setMenu(null) }}>查看日志</button>
                <button className="danger" onClick={() => { deleteSelected(); setMenu(null) }}>删除节点</button>
              </>
            )}
            {menu.kind === 'edge' && (
              <>
                <button onClick={() => { toggleEdgeDisabled(menu.id); setMenu(null) }}>停用 / 启用连线</button>
                <button onClick={() => { setDockTab('log'); setMenu(null) }}>查看日志</button>
                <div className="dropdown-sep" />
                <button className="danger" onClick={() => { setEdges(eds => eds.filter(e => e.id !== menu.id)); setMenu(null) }}>删除连线</button>
              </>
            )}
          </div>
        )
      })()}
      {edgeTip && <div style={{ position:'fixed', left: edgeTip.x + 14, top: edgeTip.y + 12, zIndex:1500, background:'var(--raise)', border:'1px solid var(--border-2)', borderRadius:10, padding:'8px 12px', fontSize: 'var(--fs-base)', pointerEvents:'none', boxShadow:'var(--shadow-2)' }} dangerouslySetInnerHTML={{ __html: edgeTip.html }} />}
    </div>
    </ErrorBoundary>
  )
}
