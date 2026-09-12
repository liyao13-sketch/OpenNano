import { useEffect, useMemo, useRef, useState } from 'react'
import type { Module } from './types'

/**
 * 批次面板：batch → runs（parent 链）→ 续做 → 表单化填写 → 菜单灌参。
 *
 * 设计口径（跨线交接单 P0）：
 * - run 序号由**工具算**（`{batch}-{stage}-{NNNN+1}`），禁手输
 * - 续做 = 新 run，parent_run_id 指向被点的那个 run（画布连线靠它）
 * - 参数键 / 量名 / 现象词 全部取自 `/api/form/contract`（schema 与受控词表）
 * - DRIE 菜单直读：在 run 上「用 group N 灌参」→ 后端调共享解析器写 steps
 */

type Ctx = {
  projectName: string
  modules: Module[]
  edges: { src: string; dst: string }[]
  onApply: (p: { name: string; modules: Module[]; edges: any[] }, log: string) => void
  onFormChange?: (modules: Module[], eqState?: any) => void
}

type Run = {
  run_id: string; stage: string; seq: number; stage_seq: number
  parent_run_id: string; status: string; tool_id: string; date: string
  title: string; recipe_id: string; module_id: string; note: string
  sample_id?: string
}
type SNode = {
  sample_id: string; parent_sample_id: string; position: string; role: string
  status: string; note: string; children: string[]; runs: string[]
  run_natures: string[]; nature_label: string
}
type ParallelGroup = {
  parent_run_id: string; stage: string; runs: string[]; count: number
  samples: string[]; distinct_samples: number; kind: string; hint: string
}

const post = async (url: string, body: any) => {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || `${r.status}`)
  return d
}

export default function BatchPanel({ ctx, onClose }: { ctx: Ctx; onClose: () => void }) {
  const [batches, setBatches] = useState<any[]>([])
  const [batch, setBatch] = useState('')
  const [chain, setChain] = useState<{ runs: Run[]; nodes: number; edges: number; roots: string[]
    parallels?: ParallelGroup[]
    natures?: { run_id: string; nature: string; nature_label: string; why: string }[]
    nature_needs_human?: string[]
    sample_tree?: { tree: SNode[]; nodes: Record<string, SNode>; count: number
                    orphan_parent: string[]; error?: string } } | null>(null)
  const [sample, setSample] = useState('')
  const [events, setEvents] = useState<{ count: number; events: any[]; plan_vs_actual: any
    consistency?: { violations: number; authority: string; note: string } } | null>(null)
  const [sel, setSel] = useState<Run | null>(null)
  const [contract, setContract] = useState<any>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [menuDir, setMenuDir] = useState('')
  const [group, setGroup] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [report, setReport] = useState('')
  const [meas, setMeas] = useState<{ quantity: string; value: string; unit: string; method: string }[]>([])
  const [obs, setObs] = useState<{ obs_type: string; description: string }[]>([])
  const [env, setEnv] = useState<any>({ date: new Date().toISOString().slice(0, 10), tool: 'RIE-400iPB', clean_done: '否' })

  const payload = useMemo(() => ({ project_name: ctx.projectName, modules: ctx.modules, edges: ctx.edges }), [ctx])

  useEffect(() => {
    post('/api/batch/list', payload).then(d => {
      setBatches(d.batches || [])
      if ((d.batches || []).length) setBatch(b => b || d.batches[d.batches.length - 1].batch_id)
    }).catch(e => setMsg('批次列表失败: ' + e.message))
    fetch('/api/form/contract').then(r => r.json()).then(setContract).catch(() => {})
    fetch('/api/menu/zones').then(r => r.json()).then(d => setMenuDir(d.default_dir || '')).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 把表单值**就地**写回画布模块（不重建画布）；导出时才能合并进包。
   *  触发条件：当前选中 run + 该 run 的表单值变化。 */
  useEffect(() => {
    if (!sel || !ctx.onFormChange) return
    const mods = ctx.modules.map(m => m.core_run_id === sel.run_id
      ? { ...m,
          core_measurements: meas.filter(x => x.quantity && String(x.value).trim() !== ''),
          core_observations: obs.filter(x => x.obs_type) }
      : m)
    ctx.onFormChange(mods)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meas, obs, sel?.run_id])

  /** 环境一行是**批次级**：存到窗口上随项目一起导出（App 侧读 __dshEqState） */
  useEffect(() => {
    if (env?.date) ctx.onFormChange?.(ctx.modules, [env])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env])

  const loadChain = async (b: string) => {
    if (!b) return
    try {
      const d = await post('/api/batch/runs', { modules: ctx.modules, batch_id: b })
      setChain(d)
      post('/api/batch/events', { batch_id: b }).then(setEvents).catch(() => {})
      const pick = d.runs[d.runs.length - 1] || null
      setSel(pick)
      if (pick) loadFormOf(pick.run_id)
    } catch (e: any) { setMsg('链加载失败: ' + e.message) }
  }
  useEffect(() => { loadChain(batch); /* eslint-disable-next-line */ }, [batch, ctx.modules.length])

  const doContinue = async (useMenu: boolean) => {
    if (!sel) return
    setBusy('continue'); setMsg('')
    try {
      /* ⚠️ 父 run 的口径（2026-09-13 由回归网查出后修正）：
         界面上"选中的 run" 与 "手填样号" 是两件事 —— 手填了样号，就**以样号为准**
         （同 stage 内找该样品的上一条 run）；此时把选中的 run 一起发过去会挂错分支。
         只有没填样号时，才把选中的 run 当作父。 */
      const wantSample = (sample || '').trim()
      const d = await post('/api/run/continue', {
        ...payload, batch_id: batch, stage: sel.stage,
        parent_run_id: wantSample ? '' : sel.run_id,
        sample_id: wantSample,
        persist: false,
        menu_group: useMenu && group ? Number(group) : null, menu_dir: menuDir,
      })
      ctx.onApply({ name: d.project.name, modules: d.project.modules, edges: d.project.edges }, d.summary)
      setMsg('✅ ' + d.summary + (d.issues?.length ? ` ⚠️ ${d.issues.length} 条校验提示` : ''))
      setSel(d.run); loadChain(batch)
    } catch (e: any) { setMsg('❌ 续做失败: ' + e.message) } finally { setBusy('') }
  }

  const scanMenu = async () => {
    setBusy('scan'); setMsg('')
    try {
      const d = await post('/api/menu/scan', { dir: menuDir })
      const named = (d.recipes || []).filter((r: any) => r.name).length
      setMsg(`菜单解析：recipe ${d.recipes?.length || 0} 槽（有名 ${named}）· group ${d.groups?.length || 0} 槽`
        + ` · 越界剔除 ${d.skipped_out_of_scope}` + (d.pair_warning ? `\n⚠️ ${d.pair_warning}` : ''))
    } catch (e: any) { setMsg('❌ 菜单扫描失败: ' + e.message) } finally { setBusy('') }
  }

  /** 统一的事件提案（kind=allocate 取样分配 / split 物理裂片）。
   *  ⚠️ 两者不可混：split 是"1 片 → N 颗"的物理事件，allocate 是"从现有样品取 N 颗"（不改总数）。 */
  const proposeEvent = async (kind: 'allocate' | 'split', apply = false) => {
    const n = Number((document.getElementById('alloc-n') as HTMLInputElement)?.value || 0)
    const to = (document.getElementById('alloc-to') as HTMLInputElement)?.value || ''
    if (!to || !n) { setMsg('请填"取样颗数"和"到样品"'); return }
    setBusy('alloc'); setMsg('')
    try {
      const d = await post('/api/batch/propose', { batch_id: batch, operator: 'owner', apply,
        events: [{ kind, at: new Date().toISOString().slice(0, 10),
                   from_sample_id: `${batch}-01`,
                   to_sample_id: kind === 'split' ? '' : to, count: n,
                   after_stage: kind === 'split' ? 'LDW' : '',
                   id_pattern: kind === 'split' ? `${batch}-01-D{n:02d}` : '',
                   note: kind === 'split' ? '工具侧：物理裂片（1 片 → N 颗）'
                                          : '工具侧：取样分配（从现有样品取 N 颗）' }] })
      const chk = d.precheck || {}
      setMsg((chk.ok ? '✅ 本地预检通过' : '❌ 本地预检未过') + `\n`
        + (chk.errors?.length ? '错误：\n' + chk.errors.join('\n') + '\n' : '')
        + (chk.warnings?.length ? '提示：\n' + chk.warnings.join('\n') + '\n' : '')
        + `\n数据线脚本（${apply ? '落账' : '干跑'}）：\n` + (d.run?.stdout || d.run?.error || ''))
      if (apply) post('/api/batch/events', { batch_id: batch }).then(setEvents).catch(() => {})
    } catch (e: any) { setMsg('❌ 提案失败: ' + e.message) } finally { setBusy('') }
  }

  const rehydrate = async () => {
    if (!batch) return
    setBusy('rehy'); setMsg('')
    try {
      const d = await post('/api/batch/rehydrate', { batch_id: batch, project_name: batch })
      ctx.onApply({ name: d.name, modules: d.modules || [], edges: d.edges || [] },
                  `从 core 回灌「${batch}」：${(d.modules || []).length} 个 run`)
      const i = d._core_to_canvas || {}
      setMsg(`✅ 已从 core 回灌「${batch}」：${i.runs} 个 run · ${i.steps} 步 · `
        + `${i.measurements} 条测量（跳过空值 ${i.measurements_blank_skipped}）· ${i.observations} 条现象\n`
        + `连线 ${(d.edges || []).length} 条（parent 链）· 只读 core，未写任何数据资产`)
    } catch (e: any) { setMsg('❌ 回灌失败: ' + e.message) } finally { setBusy('') }
  }

  const checkMenu = async () => {
    setBusy('check'); setMsg(''); setReport('')
    try {
      const d = await post('/api/menu/check', { dir: menuDir, text: true })
      setReport(d.text || '')
      setMsg(`体检：${d.summary.dumps} 份导出 · 可用 ${d.summary.ok} · 有问题 ${d.summary.with_warnings} ⇒ ${d.summary.verdict}`)
    } catch (e: any) { setMsg('❌ 体检失败: ' + e.message) } finally { setBusy('') }
  }

  const proposeMapping = async () => {
    setBusy('llm'); setMsg(''); setReport('')
    try {
      const d = await post('/api/adapter/propose', { tool: 'RIE-400iPB', dir: menuDir })
      if (d.skipped) { setMsg('✅ ' + d.note); return }
      const ok = (d.mappings || []).filter((m: any) => m.suggest)
      const human = (d.mappings || []).filter((m: any) => m.needs_human)
      setReport('LLM 提案（' + d.model + '）\n' + (d.mappings || []).map((m: any) =>
        `  ${m.column} → ${m.suggest || '（无候选）'}  conf=${m.confidence.toFixed(2)}`
        + (m.needs_human ? ' ⚠️需人工裁决' : '') + `\n     理由: ${m.reason}`).join('\n'))
      setMsg(`提案已落盘（不生效）：${ok.length} 条有候选 · ${human.length} 条需人工裁决\n`
        + `文件：server/kb/adapters/proposed/RIE-400iPB.json —— 采纳需改 datasets_menu 映射表 + §13.2 契约，再由数据线复核`)
    } catch (e: any) { setMsg('❌ 提案失败: ' + e.message) } finally { setBusy('') }
  }

  const loadGroup = async () => {
    if (!group) return
    setBusy('group'); setMsg('')
    try {
      const d = await post('/api/menu/group', { group: Number(group), dir: menuDir })
      setPreview(d)
      setMsg(`group ${d.group} = [${d.group_seq.join(', ')}] · 执行 ${d.total_steps} 步 / 定义 ${d.defined_total} 步`)
    } catch (e: any) { setMsg('❌ 取 group 失败: ' + e.message) } finally { setBusy('') }
  }

  const renderTree = (nodes: SNode[], all: Record<string, SNode>, d: number): any =>
    nodes.map(n => (
      <div key={n.sample_id} style={{ marginLeft: d * 16, padding: '2px 0',
                                      borderLeft: d ? '1px dashed var(--line,#8884)' : undefined,
                                      paddingLeft: d ? 8 : 0 }}>
        <code>{n.sample_id}</code>
        <span style={{ opacity: .8 }}>　{n.nature_label}　runs={n.runs.length}</span>
        {n.position && <span style={{ opacity: .55 }}>　{n.position}</span>}
        {n.children.length > 0 && renderTree(n.children.map(c => all[c]).filter(Boolean), all, d + 1)}
      </div>
    ))

  /** 切换 run 时把该 run 已存的表单值读回面板（否则切走再切回就空了） */
  const loadFormOf = (runId: string) => {
    const m: any = ctx.modules.find(x => x.core_run_id === runId) || {}
    setMeas((m.core_measurements || []).map((r: any) => ({
      quantity: r.quantity || '', value: String(r.value ?? ''), unit: r.unit || '',
      method: r.method || '' })))
    setObs((m.core_observations || []).map((o: any) => ({
      obs_type: o.obs_type || '', description: o.description || '' })))
  }

  const natMap: Record<string, { nature: string; nature_label: string; why: string }> = {}
  for (const n of (chain?.natures || [])) natMap[n.run_id] = n
  const runSteps = (sel && ctx.modules.find(m => m.core_run_id === sel.run_id)?.core_menu_steps) || []
  const paramJson: Record<string, any> = runSteps[0]?.param_json || {}

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal batch-panel" onClick={e => e.stopPropagation()} style={{ width: 'min(1180px, 96vw)', maxHeight: '92vh', overflow: 'auto' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>批次管理 · 续做 · 菜单直读</h3>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </div>

        <div className="row" style={{ gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
          <label>批次
            <select value={batch} onChange={e => setBatch(e.target.value)}>
              {batches.map(b => <option key={b.batch_id} value={b.batch_id}>{b.batch_id}（{b.runs} 个 run：{b.chain}）</option>)}
            </select>
          </label>
          <label>菜单目录
            <input value={menuDir} onChange={e => setMenuDir(e.target.value)} style={{ width: 380 }} />
          </label>
          <button className="btn ghost" disabled={busy !== '' || !batch} onClick={rehydrate}
            title="从 core 只读拉该 batch 的 run 链进画布（接着做的起点；不碰 CSV）">从 core 回灌画布</button>
          <button className="btn ghost" disabled={busy !== ''} onClick={scanMenu}>解析菜单目录</button>
          <button className="btn ghost" disabled={busy !== ''} onClick={checkMenu} title="批量扫该机台下所有导出：配对/未映射列/空壳/越界/跨 dump 漂移">批量体检</button>
          <button className="btn ghost" disabled={busy !== ''} onClick={proposeMapping} title="未映射列 → LLM 提规范键候选（只落提案文件，需人采纳；涉气路归属一律标 needs_human）">LLM 映射建议</button>
          <label>group N
            <input value={group} onChange={e => setGroup(e.target.value.replace(/\D/g, ''))} style={{ width: 64 }} />
          </label>
          <button className="btn ghost" disabled={!group || busy !== ''} onClick={loadGroup}>预览 group</button>
        </div>
        {msg && <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--bg2,#0002)', padding: 8, borderRadius: 6, margin: '8px 0' }}>{msg}</pre>}
        {report && <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--bg2,#0002)', padding: 8, borderRadius: 6, margin: '8px 0', maxHeight: 220, overflow: 'auto', fontSize: 12 }}>{report}</pre>}

        <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
          {/* 左：链 */}
          <div style={{ flex: '1 1 520px' }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <b>run 链</b>
              <span style={{ opacity: .7 }}>{chain ? `${chain.nodes} 节点 / ${chain.edges} 连线` : '—'}</span>
            </div>
            <table className="tbl" style={{ width: '100%', fontSize: 13 }}>
              <thead><tr><th>run_id</th><th>seq</th><th>性质</th><th>sample/die</th><th>parent</th><th>状态</th><th>recipe</th><th></th></tr></thead>
              <tbody>
                {(chain?.runs || []).map(r => (
                  <tr key={r.run_id} onClick={() => setSel(r)} style={{ cursor: 'pointer', background: sel?.run_id === r.run_id ? 'var(--sel,#0001)' : undefined }}>
                    <td>{r.run_id}</td>
                    <td>{r.stage_seq}</td>
                    <td title={natMap[r.run_id]?.why || ''} style={{ opacity: .9, whiteSpace: 'nowrap' }}>
                      {natMap[r.run_id]?.nature_label || '—'}
                    </td>
                    <td style={{ opacity: .8 }}>{r.sample_id || '—'}</td>
                    <td style={{ opacity: .75 }}>{r.parent_run_id || '—'}</td>
                    <td>{r.status}</td>
                    <td style={{ opacity: .75 }}>{r.recipe_id || '—'}</td>
                    <td><button className="btn ghost" onClick={e => { e.stopPropagation(); setSel(r) }}>选</button></td>
                  </tr>
                ))}
              </tbody>
            </table>

            {events && events.count > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 12 }}>
                <b>样品事件（裂片 / 取样分配）</b>
                <div style={{ marginTop: 2 }}>
                  计划 <b>{events.plan_vs_actual.planned ?? '—'}</b> 颗
                  {events.plan_vs_actual.id_pattern ? `（${events.plan_vs_actual.id_pattern}）` : ''}
                  · 实际用量 <b>{events.plan_vs_actual.used_top ?? 0}</b> 颗（顶层：from=整片）
                  {events.plan_vs_actual.used_within ? ` · 组内再取 ${events.plan_vs_actual.used_within} 颗` : ''}
                  {events.plan_vs_actual.unallocated != null ? ` · 未用 ${events.plan_vs_actual.unallocated} 颗` : ''}
                </div>
                {events.plan_vs_actual.planned_ids?.length > 0 && (
                  <div style={{ opacity: .6, fontSize: 11 }}>
                    计划位号：{events.plan_vs_actual.planned_ids.join(' ')}
                    <b>（应然规则；本批未在裂片时登记位号 ⇒ 实际只能到组级）</b>
                  </div>
                )}
                {events.plan_vs_actual.spec?.source_gds && (
                  <div style={{ opacity: .6, fontSize: 11 }}>版图来源：{events.plan_vs_actual.spec.source_gds}</div>
                )}
                {events.plan_vs_actual.usage_rule && (
                  <div style={{ opacity: .6, fontSize: 11 }} title={events.plan_vs_actual.usage_rule}>
                    计量规则（来自 core 的 <code>sample_spec_json.planned_use.usage_rule</code>）：
                    {events.plan_vs_actual.usage_rule.slice(0, 78)}…
                  </div>
                )}
                <table className="tbl" style={{ width: '100%', fontSize: 12, marginTop: 4 }}>
                  <thead><tr><th>event</th><th>kind</th><th>日期</th><th>从 → 到</th><th>颗数</th><th>状态</th></tr></thead>
                  <tbody>
                    {events.events.map((e: any) => (
                      <tr key={e.event_id}>
                        <td><code>{e.event_id}</code></td>
                        <td>{e.kind === 'split' ? '裂片'
                             : e.kind === 'allocate'
                               ? (events.plan_vs_actual.root_sample_id && e.from_sample_id !== events.plan_vs_actual.root_sample_id
                                  ? '取样分配（组内）' : '取样分配')
                             : e.kind}</td>
                        <td>{e.at}</td>
                        <td style={{ opacity: .8 }}>{e.from_sample_id} → {e.to_sample_id || '（组）'}</td>
                        <td>{e.count}</td><td>{e.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ opacity: .7, marginTop: 4 }}>
                  ⚠️ <b>split</b>=物理裂片（只 1 条×49）· <b>allocate</b>=从现有样品取样（不改总数）；
                  「实际用量」只算 allocate。工具**只出提案**，落账走数据线 <code>propose_apply.py</code>。
                </div>
                <div style={{ opacity: .7, marginTop: 2, fontSize: 11 }}>
                  ⚠️ <b>split 只登记事件、不建样品行</b> —— 子样品由 <code>allocate</code> 建；
                  所以裂片后样品表**不会**自动多出 N 行（没登记位号的「59 颗」不硬塞进样品表）。
                </div>
                {events.consistency && (
                  <div style={{ marginTop: 4, fontSize: 11,
                                color: events.consistency.violations ? 'var(--warn,#e8a33d)' : undefined }}>
                    一致性预览（事件 ↔ 样品树）：
                    <b>{events.consistency.violations ? `${events.consistency.violations} 项待查` : '✓ 0 项'}</b>
                    <span style={{ opacity: .6 }}>　{events.consistency.authority}</span>
                  </div>
                )}
                <div className="row" style={{ gap: 8, marginTop: 6 }}>
                  <label style={{ fontSize: 12 }}>取样颗数
                    <input id="alloc-n" defaultValue="20" style={{ width: 60, marginLeft: 4 }} /></label>
                  <label style={{ fontSize: 12 }}>到样品
                    <input id="alloc-to" placeholder={`${batch}-01-DIE20`} style={{ width: 190, marginLeft: 4 }} /></label>
                  <button className="btn ghost" onClick={() => proposeEvent('allocate', false)}
                    title="产出**取样分配**提案 → 本地预检 → 数据线 propose_apply.py 干跑（不落账）">取样提案（干跑）</button>
                  <button className="btn ghost" onClick={() => proposeEvent('split', false)}
                    title="产出**物理裂片**提案（split，1 片 → N 颗）→ 干跑">裂片提案（干跑）</button>
                  <button className="btn ghost" onClick={() => proposeEvent('allocate', true)}
                    title="真正落账（由数据线脚本执行，含幂等与不推断校验）">落账（--apply）</button>
                </div>
              </div>
            )}

            {chain?.sample_tree && !chain.sample_tree.error && chain.sample_tree.count > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 12 }}>
                <b>样品继承树（{chain.sample_tree.count} 个样品 · core 只读）</b>
                <div style={{ opacity: .7, marginBottom: 4 }}>
                  整片 → die 组 → 组内；run 挂在样品上。⚠️ 组名里的数字是<b>组内颗数</b>、不是 die 位号
                  {chain.sample_tree.orphan_parent?.length ? ` · 悬空 parent: ${chain.sample_tree.orphan_parent.join('、')}` : ''}
                </div>
                {renderTree(chain.sample_tree.tree, chain.sample_tree.nodes, 0)}
              </div>
            )}

            {chain?.nature_needs_human && chain.nature_needs_human.length > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 12,
                                             borderLeft: '3px solid var(--warn,#e8a33d)' }}>
                <b>需人工判定性质（{chain.nature_needs_human.length} 条）</b>
                <div style={{ opacity: .8 }}>
                  这些 run 无上游、也没标 sample ⇒ 可能是 <b>season 预热</b>或<b>批次级（多片一起做）</b>，
                  工具不猜：请补 <code>sample_id</code>，或在模块上标 <code>core_run_nature</code>
                  （<code>chain</code>/<code>trial</code>/<code>batch_level</code>）。
                </div>
                <div style={{ opacity: .75 }}>{chain.nature_needs_human.join('、')}</div>
              </div>
            )}

            {chain?.parallels && chain.parallels.length > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 12,
                                             borderLeft: '3px solid var(--warn,#e8a33d)' }}>
                <b>并行分支（{chain.parallels.length} 组）</b>
                {chain.parallels.map((g, i) => (
                  <div key={i} style={{ marginTop: 4 }}>
                    · <code>{g.stage}</code> × <b>{g.count}</b> 路{g.samples.length ? `（${g.distinct_samples} 个不同 sample/die）` : '（未记 sample/die）'}
                    <div style={{ opacity: .75 }}>　{g.kind}</div>
                    {g.hint && <div style={{ opacity: .75 }}>　⚠️ {g.hint}</div>}
                  </div>
                ))}
                <div style={{ opacity: .7, marginTop: 4 }}>
                  ⇒ 画布上应渲染为**同一上游下的并排分支**，不是首尾相链（避免"同一片刻了 N 次"的误读）
                </div>
              </div>
            )}

            {sel && (
              <div className="card" style={{ marginTop: 10, padding: 10 }}>
                <b>续做（从 {sel.run_id}）</b>
                <div style={{ opacity: .8, margin: '4px 0' }}>
                  将生成 <code>{batch}-{sel.stage}-{String(sel.seq + 1).padStart(4, '0')}</code>
                  ，parent 指向 <code>{sel.run_id}</code>，stage_seq 保持 {sel.stage_seq}
                </div>
                <div className="row" style={{ gap: 8, margin: '6px 0' }}>
                  <label style={{ fontSize: 12 }}>sample/die
                    <input value={sample} placeholder={sel.sample_id || '如 AR50-T1-01-DIE3'}
                      onChange={e => setSample(e.target.value)} style={{ width: 170, marginLeft: 4 }} />
                  </label>
                  <span style={{ fontSize: 11, opacity: .7 }}>
                    填了 ⇒ **只认同 sample 的上一条**（并发分支下不会挂错）；留空 ⇒ 退回"该工序最后一条"
                  </span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn" disabled={busy !== ''} onClick={() => doContinue(false)}>续做（复制参数）</button>
                  <button className="btn" disabled={busy !== '' || !group} onClick={() => doContinue(true)}>
                    续做 + 用 group {group || 'N'} 灌参
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 右：表单 */}
          <div style={{ flex: '1 1 520px' }}>
            <b>表单化填写（{sel?.run_id || '未选 run'}）</b>
            <div style={{ opacity: .75, fontSize: 12, margin: '4px 0' }}>
              键名/量名/现象词全部来自契约（schema §十三/§三 + 受控词表）；没测留空，禁填 0/-/N/A
            </div>

            <div style={{ marginTop: 8 }}>
              <b style={{ fontSize: 13 }}>步骤参数（{runSteps.length} 步{preview ? ` · 预览 group ${preview.group}` : ''}）</b>
              {runSteps.length === 0 && <div style={{ opacity: .6, fontSize: 12 }}>该 run 尚未灌参 —— 先「续做 + 用 group N 灌参」或导入实验包</div>}
              {runSteps.length > 0 && (
                <table className="tbl" style={{ width: '100%', fontSize: 12 }}>
                  <thead><tr><th>#</th><th>槽</th><th>role</th><th>时长s</th><th>参数（规范键）</th></tr></thead>
                  <tbody>
                    {runSteps.slice(0, 12).map((s: any) => (
                      <tr key={s.step_order}>
                        <td>{s.step_order}</td><td>{s.machine_step}</td><td>{s.role}</td><td>{Math.round(s.duration_s || 0)}</td>
                        <td style={{ wordBreak: 'break-all' }}>{Object.entries(s.param_json || {}).slice(0, 6).map(([k, v]) => `${k}=${v}`).join(' · ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 13 }}>测量（quantity 受控 · value 只数字）</b>
              {meas.map((m, i) => (
                <div className="row" key={i} style={{ gap: 6, margin: '4px 0' }}>
                  <select value={m.quantity} onChange={e => setMeas(a => a.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} style={{ flex: 2 }}>
                    <option value="">（选量名）</option>
                    {(contract?.quantities || []).map((q: string) => <option key={q} value={q}>{q}</option>)}
                  </select>
                  <input placeholder="值" value={m.value} onChange={e => setMeas(a => a.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} style={{ width: 90 }} />
                  <select value={m.method} onChange={e => setMeas(a => a.map((x, j) => j === i ? { ...x, method: e.target.value } : x))}>
                    <option value="">method</option>
                    {(contract?.method || []).map((q: string) => <option key={q} value={q}>{q}</option>)}
                  </select>
                  <button className="btn ghost" onClick={() => setMeas(a => a.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <button className="btn ghost" onClick={() => setMeas(a => [...a, { quantity: '', value: '', unit: '', method: '' }])}>+ 加测量</button>
            </div>

            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 13 }}>现象（obs_type 受控 32 词）</b>
              {obs.map((o, i) => (
                <div className="row" key={i} style={{ gap: 6, margin: '4px 0' }}>
                  <select value={o.obs_type} onChange={e => setObs(a => a.map((x, j) => j === i ? { ...x, obs_type: e.target.value } : x))} style={{ flex: 2 }}>
                    <option value="">（选现象）</option>
                    {(contract?.observations || []).map((v: any) => <option key={v.obs_type} value={v.obs_type}>{v.obs_type} · {v.label}</option>)}
                  </select>
                  <input placeholder="描述" value={o.description} onChange={e => setObs(a => a.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} style={{ flex: 3 }} />
                  <button className="btn ghost" onClick={() => setObs(a => a.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <button className="btn ghost" onClick={() => setObs(a => [...a, { obs_type: '', description: '' }])}>+ 加现象</button>
            </div>

            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 13 }}>上机环境（eq_state 一行）</b>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                <input value={env.date} onChange={e => setEnv({ ...env, date: e.target.value })} style={{ width: 110 }} title="YYYY-MM-DD" />
                <input value={env.tool} onChange={e => setEnv({ ...env, tool: e.target.value })} style={{ width: 120 }} title="tool_id" />
                <input placeholder="温度 ℃" value={env.env_temp_c || ''} onChange={e => setEnv({ ...env, env_temp_c: e.target.value })} style={{ width: 80 }} />
                <input placeholder="湿度 %" value={env.env_rh_pct || ''} onChange={e => setEnv({ ...env, env_rh_pct: e.target.value })} style={{ width: 80 }} />
                <input placeholder="本底 Pa" value={env.chamber_bg_pa || ''} onChange={e => setEnv({ ...env, chamber_bg_pa: e.target.value })} style={{ width: 90 }} />
                <input placeholder="Chiller ℃" value={env.chiller_temp_c || ''} onChange={e => setEnv({ ...env, chiller_temp_c: e.target.value })} style={{ width: 90 }} />
                <select value={env.clean_done} onChange={e => setEnv({ ...env, clean_done: e.target.value })}>
                  <option value="否">未清扫</option><option value="是">已清扫/seasoning</option>
                </select>
              </div>
            </div>

            <div style={{ marginTop: 10, opacity: .65, fontSize: 12 }}>
              参数键示例：{Object.entries(contract?.param_keys || {}).slice(0, 6).map(([k, v]: any) => `${k}←${v.from}`).join(' · ')}
              {paramJson && Object.keys(paramJson).length > 0 && <>　｜　当前步示例：{Object.entries(paramJson).slice(0, 4).map(([k, v]) => `${k}=${v}`).join(' · ')}</>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
