import { useEffect, useMemo, useState } from 'react'
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
}

type Run = {
  run_id: string; stage: string; seq: number; stage_seq: number
  parent_run_id: string; status: string; tool_id: string; date: string
  title: string; recipe_id: string; module_id: string; note: string
  sample_id?: string
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
    nature_needs_human?: string[] } | null>(null)
  const [sample, setSample] = useState('')
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

  const loadChain = async (b: string) => {
    if (!b) return
    try {
      const d = await post('/api/batch/runs', { modules: ctx.modules, batch_id: b })
      setChain(d); setSel(d.runs[d.runs.length - 1] || null)
    } catch (e: any) { setMsg('链加载失败: ' + e.message) }
  }
  useEffect(() => { loadChain(batch); /* eslint-disable-next-line */ }, [batch, ctx.modules.length])

  const doContinue = async (useMenu: boolean) => {
    if (!sel) return
    setBusy('continue'); setMsg('')
    try {
      const d = await post('/api/run/continue', {
        ...payload, batch_id: batch, stage: sel.stage,
        parent_run_id: sel.run_id, sample_id: sample || (sel as any).sample_id || '', persist: false,
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

  const natMap: Record<string, { nature: string; nature_label: string; why: string }> = {}
  for (const n of (chain?.natures || [])) natMap[n.run_id] = n
  const runSteps = (sel && (ctx.modules.find(m => m.core_run_id === sel.run_id) as any)?.core_menu_steps) || []
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
