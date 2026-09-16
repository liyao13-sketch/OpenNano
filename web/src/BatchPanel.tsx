import { useI18n } from './i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Module } from './types'
import TuneLineView from './TuneLineView'

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
  const { t: tr } = useI18n()
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
  /** 表单当前装载的是哪个 run（与 `sel` 分开：两者不一致时**禁止写回画布**，见下方 effect）。 */
  const [formRun, setFormRun] = useState('')
  const [env, setEnv] = useState<any>({ date: new Date().toISOString().slice(0, 10), tool: 'RIE-400iPB', clean_done: '否' })   // i18n-keep：eq_state.clean_done 的取值（core 枚举），下拉选项的 value 与它对齐
  const [showSeason, setShowSeason] = useState(false)          // season 默认不画（owner 2026-09-12 裁断）
  const [tuneLine, setTuneLine] = useState<any>(null)          // v_tune_line（数据线视图）
  const [toolsOpen, setToolsOpen] = useState(false)            // 菜单工具下拉

  const payload = useMemo(() => ({ project_name: ctx.projectName, modules: ctx.modules, edges: ctx.edges }), [ctx])

  useEffect(() => {
    post('/api/batch/list', payload).then(d => {
      setBatches(d.batches || [])
      if ((d.batches || []).length) setBatch(b => b || d.batches[d.batches.length - 1].batch_id)
    }).catch(e => setMsg(tr('bd.batchListFail', { msg: e.message })))
    fetch('/api/form/contract').then(r => r.json()).then(setContract).catch(() => {})
    fetch('/api/menu/zones').then(r => r.json()).then(d => setMenuDir(d.default_dir || '')).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 把表单值**就地**写回画布模块（不重建画布）；导出时才能合并进包。
   *  触发条件：当前选中 run + 该 run 的表单值变化。
   *  ⚠️ 必须同时满足 `formRun === sel.run_id`（2026-09-16 审计 P0）：否则"换了选中 run 但表单
   *     还装着上一个 run"的那一瞬，会把**上一个 run 的测量值写进新 run 的模块**，
   *     而且 save/导出/追加包全都消费画布模块 ⇒ 错位测量值直接落盘。 */
  useEffect(() => {
    if (!sel || !ctx.onFormChange) return
    if (formRun !== sel.run_id) return          // 表单没装载到这个 run ⇒ 一个字都不写
    const mods = ctx.modules.map(m => m.core_run_id === sel.run_id
      ? { ...m,
          core_measurements: meas.filter(x => x.quantity && String(x.value).trim() !== ''),
          core_observations: obs.filter(x => x.obs_type) }
      : m)
    ctx.onFormChange(mods)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meas, obs, sel?.run_id, formRun])

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
      post('/api/batch/tune_line', { modules: ctx.modules, batch_id: b })
        .then(setTuneLine).catch(() => setTuneLine(null))
      const pick = d.runs[d.runs.length - 1] || null
      setSel(pick)
      if (pick) loadFormOf(pick.run_id)
    } catch (e: any) { setMsg(tr('bd.chainFail', { msg: e.message })) }
  }
  /* 短号：只去批次前缀（`AR50-T1-ICP-0013` → `ICP-0013`）。
     与画布节点同一约定 —— 表里 8 列，长号会把「状态」挤出可视区（2026-09-13 owner报）。 */
  const short = (v?: string | null) => {
    const x = v || ''
    return batch && x.startsWith(batch + '-') ? x.slice(batch.length + 1) : x
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
      setMsg('✅ ' + d.summary + (d.issues?.length ? ` ⚠️ ${d.issues.length}` : ''))
      setSel(d.run); loadChain(batch)
    } catch (e: any) { setMsg(tr('bd.continueFail', { msg: e.message })) } finally { setBusy('') }
  }

  const scanMenu = async () => {
    setBusy('scan'); setMsg('')
    try {
      const d = await post('/api/menu/scan', { dir: menuDir })
      const named = (d.recipes || []).filter((r: any) => r.name).length
      setMsg(tr('bd.menuScan', { rcp: d.recipes?.length || 0, named, grp: d.groups?.length || 0, skipped: d.skipped_out_of_scope })
        + (d.pair_warning ? `\n⚠️ ${d.pair_warning}` : ''))
    } catch (e: any) { setMsg(tr('bd.menuScanFail', { msg: e.message })) } finally { setBusy('') }
  }

  /** 统一的事件提案（kind=allocate 取样分配 / split 物理裂片）。
   *  ⚠️ 两者不可混：split 是"1 片 → N 颗"的物理事件，allocate 是"从现有样品取 N 颗"（不改总数）。 */
  const proposeEvent = async (kind: 'allocate' | 'split', apply = false) => {
    const n = Number((document.getElementById('alloc-n') as HTMLInputElement)?.value || 0)
    const to = (document.getElementById('alloc-to') as HTMLInputElement)?.value || ''
    if (!to || !n) { setMsg(tr('bd.needFields')); return }
    setBusy('alloc'); setMsg('')
    try {
      const d = await post('/api/batch/propose', { batch_id: batch, operator: 'owner', apply,   // i18n-keep：operator 是写进提案记录的人名
        events: [{ kind, at: new Date().toISOString().slice(0, 10),
                   from_sample_id: `${batch}-01`,
                   to_sample_id: kind === 'split' ? '' : to, count: n,
                   after_stage: kind === 'split' ? 'LDW' : '',
                   id_pattern: kind === 'split' ? `${batch}-01-D{n:02d}` : '',
                   note: kind === 'split' ? '工具侧：物理裂片（1 片 → N 颗）'   // i18n-keep：note 原样落进提案记录（数据线要读）
                                          : '工具侧：取样分配（从现有样品取 N 颗）' }] })   // i18n-keep：同上
      const chk = d.precheck || {}
      setMsg((chk.ok ? tr('bd.precheckOk') : tr('bd.precheckFail')) + `\n`
        + (chk.errors?.length ? tr('bd.errors') + '\n' + chk.errors.join('\n') + '\n' : '')
        + (chk.warnings?.length ? tr('bd.warnings') + '\n' + chk.warnings.join('\n') + '\n' : '')
        + `\n${apply ? tr('bd.scriptApply') : tr('bd.scriptDry')}\n` + (d.run?.stdout || d.run?.error || ''))
      if (apply) post('/api/batch/events', { batch_id: batch }).then(setEvents).catch(() => {})
    } catch (e: any) { setMsg(tr('bd.proposeFail', { msg: e.message })) } finally { setBusy('') }
  }

  const rehydrate = async () => {
    if (!batch) return
    setBusy('rehy'); setMsg('')
    try {
      const d = await post('/api/batch/rehydrate', { batch_id: batch, project_name: batch })
      ctx.onApply({ name: d.name, modules: d.modules || [], edges: d.edges || [] },
                  tr('bd.rehydrateLog', { batch, n: (d.modules || []).length }))
      const i = d._core_to_canvas || {}
      setMsg(tr('bd.rehydrated', { batch, runs: i.runs, steps: i.steps, meas: i.measurements,
          blank: i.measurements_blank_skipped, obs: i.observations }) + '\n'
        + tr('bd.rehydrateEdges', { n: (d.edges || []).length }))
    } catch (e: any) { setMsg(tr('bd.rehydrateFail', { msg: e.message })) } finally { setBusy('') }
  }

  const checkMenu = async () => {
    setBusy('check'); setMsg(''); setReport('')
    try {
      const d = await post('/api/menu/check', { dir: menuDir, text: true })
      setReport(d.text || '')
      setMsg(tr('bd.inspect', { dumps: d.summary.dumps, ok: d.summary.ok, bad: d.summary.with_warnings, verdict: d.summary.verdict }))
    } catch (e: any) { setMsg(tr('bd.inspectFail', { msg: e.message })) } finally { setBusy('') }
  }

  const proposeMapping = async () => {
    setBusy('llm'); setMsg(''); setReport('')
    try {
      const d = await post('/api/adapter/propose', { tool: 'RIE-400iPB', dir: menuDir })
      if (d.skipped) { setMsg('✅ ' + d.note); return }
      const ok = (d.mappings || []).filter((m: any) => m.suggest)
      const human = (d.mappings || []).filter((m: any) => m.needs_human)
      setReport(tr('bd.llmReport', { model: d.model }) + '\n' + (d.mappings || []).map((m: any) =>
        `  ${m.column} ${tr('bd.llmCandidate', { suggest: m.suggest || tr('bd.noCandidate') })}  conf=${m.confidence.toFixed(2)}`
        + (m.needs_human ? ` ${tr('bd.needsHuman')}` : '') + `\n     ${tr('bd.reason', { text: m.reason })}`).join('\n'))
      setMsg(tr('bd.proposalSaved', { ok: ok.length, human: human.length }) + '\n' + tr('bd.proposalFile'))
    } catch (e: any) { setMsg(tr('bd.proposeFail', { msg: e.message })) } finally { setBusy('') }
  }

  const loadGroup = async () => {
    if (!group) return
    setBusy('group'); setMsg('')
    try {
      const d = await post('/api/menu/group', { group: Number(group), dir: menuDir })
      setPreview(d)
      setMsg(tr('bd.groupInfo', { g: d.group, seq: d.group_seq.join(', '), exec: d.total_steps, defined: d.defined_total }))
    } catch (e: any) { setMsg(tr('bd.groupFail', { msg: e.message })) } finally { setBusy('') }
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
    setFormRun(runId)          // 标记"表单装的是这个 run"（写回 effect 的门闩）
  }

  /** 选中一个 run 的唯一入口：**先装载表单、再换选中** —— 顺序不能反（见上方 P0 注释）。 */
  const selectRun = (r: Run) => {
    if (!r?.run_id) return
    loadFormOf(r.run_id)
    setSel(r)
  }

  const natMap: Record<string, { nature: string; nature_label: string; why: string }> = {}
  for (const n of (chain?.natures || [])) natMap[n.run_id] = n
  const runSteps = (sel && ctx.modules.find(m => m.core_run_id === sel.run_id)?.core_menu_steps) || []
  const paramJson: Record<string, any> = runSteps[0]?.param_json || {}

  const natOf = (rid: string) => natMap[rid]?.nature || ''
  const seasonRuns = (chain?.runs || []).filter(r => natOf(r.run_id) === 'season')
  const visibleRuns = (chain?.runs || []).filter(r => showSeason || natOf(r.run_id) !== 'season')

  return (
    <div className="batchdock">
      {/* 工具条：批次 + 回灌 + 菜单工具收纳（原 7 个按钮一字排开 ⇒ 收成 3 个） */}
      <div className="bd-toolbar">
        <label>{tr('bd.batchLabel2')}
          <select value={batch} onChange={e => setBatch(e.target.value)}>
            {batches.map(b => <option key={b.batch_id} value={b.batch_id}>{tr('bd.runCount', { id: b.batch_id, n: b.runs })}</option>)}
          </select>
        </label>
        <button className="btn ghost" disabled={busy !== '' || !batch} onClick={rehydrate}
          title={tr('bd.rehydrateTip')}>{tr('bd.rehydrate')}</button>
        <div style={{ position: 'relative' }}>
          <button className="btn ghost" onClick={() => setToolsOpen(v => !v)}>{tr('bd.tools')}</button>
          {toolsOpen && (
            <div className="dropdown" onMouseLeave={() => setToolsOpen(false)}>
              <div className="dd-sec">{tr('bd.menuDir')}</div>
              <div style={{ padding: '2px 9px 6px' }}>
                <input value={menuDir} onChange={e => setMenuDir(e.target.value)}
                  style={{ width: 300 }} placeholder={tr('bd.menuDirPh')} />
              </div>
              <button className="dropdown-item" disabled={busy !== ''}
                onClick={() => { setToolsOpen(false); scanMenu() }}>{tr('bd.parseMenu')}</button>
              <button className="dropdown-item" disabled={busy !== ''}
                onClick={() => { setToolsOpen(false); checkMenu() }}
                title={tr('bd.bulkInspectTip')}>{tr('bd.bulkInspect')}</button>
              <button className="dropdown-item" disabled={busy !== ''}
                onClick={() => { setToolsOpen(false); proposeMapping() }}
                title={tr('bd.llmTip')}>{tr('bd.llmBtn')}</button>
              <div className="dropdown-sep" />
              <div className="dd-sec">{tr('bd.groupPreview')}</div>
              <div style={{ display: 'flex', gap: 6, padding: '2px 9px 6px', alignItems: 'center' }}>
                <input value={group} onChange={e => setGroup(e.target.value.replace(/\D/g, ''))}
                  style={{ width: 64 }} placeholder="N" />
                <button className="dropdown-item" style={{ flex: 1 }} disabled={!group || busy !== ''}
                  onClick={() => { setToolsOpen(false); loadGroup() }}>{tr('bd.previewGroup')}</button>
              </div>
            </div>
          )}
        </div>
        <span className="spacer" />
        {onClose && <button className="btn ghost" onClick={onClose} title={tr('bd.closePanel')}>✕</button>}
      </div>
      {msg && <div className="bd-msg">{msg}</div>}
      {report && <pre className="bd-report">{report}</pre>}

      {/* 左格放宽：run 链那张表 8 列，50/50 平分会挤到只能横向滚（2026-09-13） */}
      <div className="bd-panes">
        {/* 左：链 + 调试线 + 事件 + 样品树 + 续做 */}
        <div className="bd-pane wide">
          <div className="bd-sec-head">
            <b>{tr('bd.runChain')}</b>
            <span className="dim">
              {chain ? tr('bd.nodesEdges', { n: chain.nodes, e: chain.edges }) : '—'}
              {seasonRuns.length > 0 && (
                <label style={{ marginLeft: 10, cursor: 'pointer' }}
                  title={tr('bd.seasonTip')}>
                  <input type="checkbox" checked={showSeason}
                    onChange={e => setShowSeason(e.target.checked)} />
                  {tr('bd.showSeason', { n: seasonRuns.length })}
                </label>
              )}
            </span>
          </div>
          {/* 字号只在 `.tbl` 定一次；单元格一律不换行（窄了横向滚动）—— 见 styles.css「表格排版统一」 */}
          <div className="tbl-wrap">
            <table className="tbl" style={{ width: '100%' }}>
              {/* 列序按"看得见的优先级"排：状态排在 parent/recipe 之前 —— 窄了横向滚时也不会被挤出屏幕 */}
              <thead><tr><th>run_id</th><th>{tr('bd.colSeq')}</th><th>{tr('bd.colNature')}</th><th>sample</th><th>{tr('bd.colState')}</th><th>parent</th><th>recipe</th><th></th></tr></thead>
              <tbody>
                {visibleRuns.map(r => (
                  <tr key={r.run_id} onClick={() => selectRun(r)} style={{ cursor: 'pointer', background: sel?.run_id === r.run_id ? 'var(--sel,#0001)' : undefined }}>
                    <td className="code">{r.run_id}</td>
                    <td>{r.stage_seq}</td>
                    {/* 长标签只留主干（全文进 tooltip）：**中英文括号都要切** —— 只切全角曾漏掉「批次级(多片同做)」 */}
                    <td className="soft" title={natMap[r.run_id]?.why || ''}>
                      {(natMap[r.run_id]?.nature_label || '—').split(/[（(]/)[0]}</td>
                    <td className="soft" title={r.sample_id || ''}>{short(r.sample_id) || '—'}</td>
                    <td>{r.status}</td>
                    <td className="soft" title={r.parent_run_id || ''}>{short(r.parent_run_id) || '—'}</td>
                    <td className="soft ellip" title={r.recipe_id || ''}>{short((r.recipe_id || '').replace(/^core:/, '')) || '—'}</td>
                    <td><button className="btn ghost sm" onClick={e => { e.stopPropagation(); selectRun(r) }}>{tr('bd.pick')}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

            {/* 参数调试线（O1）：数据来自数据线的 v_tune_line 视图（缺 = NULL、不补值） */}
            {tuneLine?.available && (tuneLine.series || []).length > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 10 }}>
                <div className="bd-sec-head">
                  <b>{tr('bd.tuneLine')}</b>
                  <span className="dim">{tuneLine.source}</span>
                </div>
                {tuneLine.series.map((s: any) => <TuneLineView key={s.tune_id} series={s} />)}
              </div>
            )}
            {tuneLine && !tuneLine.available && (
              <div className="card" style={{ marginTop: 8, padding: 10, fontSize: 'var(--fs-base)', opacity: .75 }}>
                <b>{tr('bd.tuneUnavailable')}</b>: {tuneLine.reason}
              </div>
            )}

            {events && events.count > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 'var(--fs-base)' }}>
                <b>{tr('bd.events')}</b>
                <div style={{ marginTop: 2 }}>
                  {tr('bd.planned')} <b>{events.plan_vs_actual.planned ?? '—'}</b> {tr('bd.pieces')}
                  {events.plan_vs_actual.id_pattern ? ` (${events.plan_vs_actual.id_pattern})` : ''}
                  · {tr('bd.actualUse')} <b>{events.plan_vs_actual.used_top ?? 0}</b> {tr('bd.pieces')} ({tr('bd.topLevel')})
                  {events.plan_vs_actual.used_within ? ` ${tr('bd.withinGroup', { n: events.plan_vs_actual.used_within })}` : ''}
                  {events.plan_vs_actual.unallocated != null ? ` ${tr('bd.unused', { n: events.plan_vs_actual.unallocated })}` : ''}
                </div>
                {events.plan_vs_actual.planned_ids?.length > 0 && (
                  <div style={{ opacity: .6, fontSize: 'var(--fs-xs)' }}>
                    {tr('bd.plannedIds')} {events.plan_vs_actual.planned_ids.join(' ')}
                    <b>{tr('bd.rulesNote')}</b>
                  </div>
                )}
                {events.plan_vs_actual.spec?.source_gds && (
                  <div style={{ opacity: .6, fontSize: 'var(--fs-xs)' }}>{tr('bd.layoutSource')} {events.plan_vs_actual.spec.source_gds}</div>
                )}
                {events.plan_vs_actual.usage_rule && (
                  <div style={{ opacity: .6, fontSize: 'var(--fs-xs)' }} title={events.plan_vs_actual.usage_rule}>
                    {tr('bd.usageRule')} <code>sample_spec_json.planned_use.usage_rule</code>):
                    {events.plan_vs_actual.usage_rule.slice(0, 78)}…
                  </div>
                )}
                <div className="tbl-wrap" style={{ marginTop: 4 }}>
                <table className="tbl" style={{ width: '100%' }}>
                  <thead><tr><th>event</th><th>kind</th><th>{tr('bd.date')}</th><th>{tr('bd.fromTo')}</th><th>{tr('bd.count')}</th><th>{tr('bd.colStatus')}</th></tr></thead>
                  <tbody>
                    {events.events.map((e: any) => (
                      <tr key={e.event_id}>
                        <td><code>{e.event_id}</code></td>
                        <td>{e.kind === 'split' ? tr('bd.split')
                             : e.kind === 'allocate'
                               ? (events.plan_vs_actual.root_sample_id && e.from_sample_id !== events.plan_vs_actual.root_sample_id
                                  ? tr('bd.allocateWithin') : tr('bd.allocate'))
                             : e.kind}</td>
                        <td>{e.at}</td>
                        <td className="soft">{e.from_sample_id} → {e.to_sample_id || tr('bd.groupOf')}</td>
                        <td>{e.count}</td><td>{e.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
                <div style={{ opacity: .7, marginTop: 4 }}>
                  {tr('bd.splitLegend')}
                  {tr('bd.usageNote1')} <code>propose_apply.py</code>{tr('bd.usageNote2')}
                </div>
                <div style={{ opacity: .7, marginTop: 2, fontSize: 'var(--fs-xs)' }}>
                  ⚠️ <b>{tr('bd.splitNote1')}</b> {tr('bd.splitNote2')} <code>allocate</code>; {tr('bd.splitNote3')}
                </div>
                {events.consistency && (
                  <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)',
                                color: events.consistency.violations ? 'var(--warn)' : undefined }}>
                    {tr('bd.consistency')} <b>{events.consistency.violations ? tr('bd.consistencyCheck', { n: events.consistency.violations }) : tr('bd.consistencyOk')}</b>
                    <span style={{ opacity: .6 }}>　{events.consistency.authority}</span>
                  </div>
                )}
                <div className="row" style={{ gap: 8, marginTop: 6 }}>
                  <label style={{ fontSize: 'var(--fs-base)' }}>{tr('bd.countLabel')}
                    <input id="alloc-n" defaultValue="20" style={{ width: 60, marginLeft: 4 }} /></label>
                  <label style={{ fontSize: 'var(--fs-base)' }}>{tr('bd.toSample')}
                    <input id="alloc-to" placeholder={`${batch}-01-DIE20`} style={{ width: 190, marginLeft: 4 }} /></label>
                  <button className="btn ghost" onClick={() => proposeEvent('allocate', false)}
                    title={tr('bd.allocateBtnTip')}>{tr('bd.allocateBtn')}</button>
                  <button className="btn ghost" onClick={() => proposeEvent('split', false)}
                    title={tr('bd.splitBtnTip')}>{tr('bd.splitBtn')}</button>
                  <button className="btn ghost" onClick={() => proposeEvent('allocate', true)}
                    title={tr('bd.applyBtnTip')}>{tr('bd.applyBtn')}</button>
                </div>
              </div>
            )}

            {chain?.sample_tree && !chain.sample_tree.error && chain.sample_tree.count > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 'var(--fs-base)' }}>
                <b>{tr('bd.sampleTree', { n: chain.sample_tree.count })}</b>
                <div style={{ opacity: .7, marginBottom: 4 }}>
                  {tr('bd.treeNote1')} <b>{tr('bd.treeNote2')}</b> {tr('bd.treeNote3')}
                  {chain.sample_tree.orphan_parent?.length ? ` · ${tr('bd.orphanParent', { list: chain.sample_tree.orphan_parent.join(', ') })}` : ''}
                </div>
                {renderTree(chain.sample_tree.tree, chain.sample_tree.nodes, 0)}
              </div>
            )}

            {chain?.nature_needs_human && chain.nature_needs_human.length > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 'var(--fs-base)',
                                             borderLeft: '3px solid var(--warn)' }}>
                <b>{tr('bd.natureHuman', { n: chain.nature_needs_human.length })}</b>
                <div style={{ opacity: .8 }}>
                  {tr('bd.orphanNote1')} <b>{tr('bd.orphanNote2')}</b> {tr('bd.orphanNote3')} <b>{tr('bd.orphanNote4')}</b>{tr('bd.orphanNote5')}
                  {tr('bd.natureFix1')} <code>sample_id</code>{tr('bd.natureFix2')} <code>core_run_nature</code> (<code>chain</code>/<code>trial</code>/<code>batch_level</code>).
                </div>
                <div style={{ opacity: .75 }}>{chain.nature_needs_human.join(', ')}</div>
              </div>
            )}

            {chain?.parallels && chain.parallels.length > 0 && (
              <div className="card" style={{ marginTop: 8, padding: 8, fontSize: 'var(--fs-base)',
                                             borderLeft: '3px solid var(--warn)' }}>
                <b>{tr('bd.parHead', { n: chain.parallels.length })}</b>
                {chain.parallels.map((g, i) => (
                  <div key={i} style={{ marginTop: 4 }}>
                    · <code>{g.stage}</code> × <b>{g.count}</b>{tr('bd.parBranches')} {g.samples.length ? tr('bd.parDistinct', { n: g.distinct_samples }) : tr('bd.parNoSample')}
                    <div style={{ opacity: .75 }}>　{g.kind}</div>
                    {g.hint && <div style={{ opacity: .75 }}>　⚠️ {g.hint}</div>}
                  </div>
                ))}
                <div style={{ opacity: .7, marginTop: 4 }}>
                  {tr('bd.parNote')}
                </div>
              </div>
            )}

            {sel && (
              <div className="card" style={{ marginTop: 10, padding: 10 }}>
                <b>{tr('bd.continueFrom', { run: sel.run_id })}</b>
                <div style={{ opacity: .8, margin: '4px 0' }}>
                  {tr('bd.continueWill1')} <code>{batch}-{sel.stage}-{String(sel.seq + 1).padStart(4, '0')}</code>{tr('bd.continueWill2')} <code>{sel.run_id}</code>{tr('bd.continueWill3', { seq: sel.stage_seq })}
                </div>
                <div className="row" style={{ gap: 8, margin: '6px 0' }}>
                  <label style={{ fontSize: 'var(--fs-base)' }}>sample/die
                    <input value={sample} placeholder={sel.sample_id || tr('bd.samplePh')}
                      onChange={e => setSample(e.target.value)} style={{ width: 170, marginLeft: 4 }} />
                  </label>
                  <span style={{ fontSize: 'var(--fs-xs)', opacity: .7 }}>
                    {tr('bd.sampleRule')}
                  </span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn" disabled={busy !== ''} onClick={() => doContinue(false)}>{tr('bd.continue')}</button>
                  <button className="btn" disabled={busy !== '' || !group} onClick={() => doContinue(true)}>
                    {tr('bd.continueWithGroup', { g: group || 'N' })}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 右：表单 */}
          <div className="bd-pane">
            <b>{tr('bd.formHead', { run: sel?.run_id || tr('bd.noRunSel') })}</b>
            <div style={{ opacity: .75, fontSize: 'var(--fs-base)', margin: '4px 0' }}>
              {tr('bd.formNote1')}schema §十三/§三{tr('bd.formNote2')}{/* i18n-keep：§十三/§三 是 schema 文档的小节号，不许改成 §13/§3 */}
            </div>

            <div style={{ marginTop: 8 }}>
              <b style={{ fontSize: 'var(--fs-md)' }}>{tr('bd.stepsHead', { n: runSteps.length })}{preview ? tr('bd.stepsPreview', { g: preview.group }) : ''}</b>
              {runSteps.length === 0 && <div style={{ opacity: .6, fontSize: 'var(--fs-base)' }}>{tr('bd.noSteps')}</div>}
              {runSteps.length > 0 && (
                <div className="tbl-wrap">
                  <table className="tbl" style={{ width: '100%' }}>
                    <thead><tr><th>#</th><th>{tr('bd.colSlot')}</th><th>role</th><th>{tr('bd.colDur')}</th><th>{tr('bd.colParams')}</th></tr></thead>
                    <tbody>
                      {runSteps.slice(0, 12).map((s: any) => (
                        <tr key={s.step_order}>
                          <td>{s.step_order}</td><td>{s.machine_step}</td><td>{s.role}</td><td>{Math.round(s.duration_s || 0)}</td>
                          <td className="wrap">{Object.entries(s.param_json || {}).slice(0, 6).map(([k, v]) => `${k}=${v}`).join(' · ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 'var(--fs-md)' }}>{tr('bd.measHead')}</b>
              {meas.map((m, i) => (
                <div className="row" key={i} style={{ gap: 6, margin: '4px 0' }}>
                  <select value={m.quantity} onChange={e => setMeas(a => a.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} style={{ flex: 2 }}>
                    <option value="">{tr('bd.pickQuantity')}</option>
                    {(contract?.quantities || []).map((q: string) => <option key={q} value={q}>{q}</option>)}
                  </select>
                  <input placeholder={tr('bd.valuePh')} value={m.value} onChange={e => setMeas(a => a.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} style={{ width: 90 }} />
                  <select value={m.method} onChange={e => setMeas(a => a.map((x, j) => j === i ? { ...x, method: e.target.value } : x))}>
                    <option value="">method</option>
                    {(contract?.method || []).map((q: string) => <option key={q} value={q}>{q}</option>)}
                  </select>
                  <button className="btn ghost" onClick={() => setMeas(a => a.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <button className="btn ghost" onClick={() => setMeas(a => [...a, { quantity: '', value: '', unit: '', method: '' }])}>{tr('bd.addMeas')}</button>
            </div>

            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 'var(--fs-md)' }}>{tr('bd.obsHead')}</b>
              {obs.map((o, i) => (
                <div className="row" key={i} style={{ gap: 6, margin: '4px 0' }}>
                  <select value={o.obs_type} onChange={e => setObs(a => a.map((x, j) => j === i ? { ...x, obs_type: e.target.value } : x))} style={{ flex: 2 }}>
                    <option value="">{tr('bd.pickObs')}</option>
                    {(contract?.observations || []).map((v: any) => <option key={v.obs_type} value={v.obs_type}>{v.obs_type} · {v.label}</option>)}
                  </select>
                  <input placeholder={tr('bd.descPh')} value={o.description} onChange={e => setObs(a => a.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} style={{ flex: 3 }} />
                  <button className="btn ghost" onClick={() => setObs(a => a.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <button className="btn ghost" onClick={() => setObs(a => [...a, { obs_type: '', description: '' }])}>{tr('bd.addObs')}</button>
            </div>

            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 'var(--fs-md)' }}>{tr('bd.envHead')}</b>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                <input value={env.date} onChange={e => setEnv({ ...env, date: e.target.value })} style={{ width: 110 }} title="YYYY-MM-DD" />
                <input value={env.tool} onChange={e => setEnv({ ...env, tool: e.target.value })} style={{ width: 120 }} title="tool_id" />
                <input placeholder={tr('bd.tempPh')} value={env.env_temp_c || ''} onChange={e => setEnv({ ...env, env_temp_c: e.target.value })} style={{ width: 80 }} />
                <input placeholder={tr('bd.rhPh')} value={env.env_rh_pct || ''} onChange={e => setEnv({ ...env, env_rh_pct: e.target.value })} style={{ width: 80 }} />
                <input placeholder={tr('bd.bgPh')} value={env.chamber_bg_pa || ''} onChange={e => setEnv({ ...env, chamber_bg_pa: e.target.value })} style={{ width: 90 }} />
                <input placeholder="Chiller ℃" value={env.chiller_temp_c || ''} onChange={e => setEnv({ ...env, chiller_temp_c: e.target.value })} style={{ width: 90 }} />
                <select value={env.clean_done} onChange={e => setEnv({ ...env, clean_done: e.target.value })}>
                  <option value="否">{tr('bd.envNo')}</option><option value="是">{tr('bd.envYes')}</option>{/* i18n-keep：value 是 eq_state.clean_done 的 core 取值，只有可见文案走取词 */}
                </select>
              </div>
            </div>

            <div style={{ marginTop: 10, opacity: .65, fontSize: 'var(--fs-base)' }}>
              {tr('bd.paramKeyEg')} {Object.entries(contract?.param_keys || {}).slice(0, 6).map(([k, v]: any) => `${k}←${v.from}`).join(' · ')}
              {paramJson && Object.keys(paramJson).length > 0 && <> · {tr('bd.curStepEg')} {Object.entries(paramJson).slice(0, 4).map(([k, v]) => `${k}=${v}`).join(' · ')}</>}
            </div>
          </div>
        </div>
      </div>
  )
}
