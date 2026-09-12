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
  const [chain, setChain] = useState<{ runs: Run[]; nodes: number; edges: number; roots: string[] } | null>(null)
  const [sel, setSel] = useState<Run | null>(null)
  const [contract, setContract] = useState<any>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [menuDir, setMenuDir] = useState('')
  const [group, setGroup] = useState('')
  const [preview, setPreview] = useState<any>(null)
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
        parent_run_id: sel.run_id, persist: false,
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

  const loadGroup = async () => {
    if (!group) return
    setBusy('group'); setMsg('')
    try {
      const d = await post('/api/menu/group', { group: Number(group), dir: menuDir })
      setPreview(d)
      setMsg(`group ${d.group} = [${d.group_seq.join(', ')}] · 执行 ${d.total_steps} 步 / 定义 ${d.defined_total} 步`)
    } catch (e: any) { setMsg('❌ 取 group 失败: ' + e.message) } finally { setBusy('') }
  }

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
          <label>group N
            <input value={group} onChange={e => setGroup(e.target.value.replace(/\D/g, ''))} style={{ width: 64 }} />
          </label>
          <button className="btn ghost" disabled={!group || busy !== ''} onClick={loadGroup}>预览 group</button>
        </div>
        {msg && <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--bg2,#0002)', padding: 8, borderRadius: 6, margin: '8px 0' }}>{msg}</pre>}

        <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
          {/* 左：链 */}
          <div style={{ flex: '1 1 520px' }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <b>run 链</b>
              <span style={{ opacity: .7 }}>{chain ? `${chain.nodes} 节点 / ${chain.edges} 连线` : '—'}</span>
            </div>
            <table className="tbl" style={{ width: '100%', fontSize: 13 }}>
              <thead><tr><th>run_id</th><th>seq</th><th>parent</th><th>状态</th><th>recipe</th><th></th></tr></thead>
              <tbody>
                {(chain?.runs || []).map(r => (
                  <tr key={r.run_id} onClick={() => setSel(r)} style={{ cursor: 'pointer', background: sel?.run_id === r.run_id ? 'var(--sel,#0001)' : undefined }}>
                    <td>{r.run_id}</td>
                    <td>{r.stage_seq}</td>
                    <td style={{ opacity: .75 }}>{r.parent_run_id || '—'}</td>
                    <td>{r.status}</td>
                    <td style={{ opacity: .75 }}>{r.recipe_id || '—'}</td>
                    <td><button className="btn ghost" onClick={e => { e.stopPropagation(); setSel(r) }}>选</button></td>
                  </tr>
                ))}
              </tbody>
            </table>

            {sel && (
              <div className="card" style={{ marginTop: 10, padding: 10 }}>
                <b>续做（从 {sel.run_id}）</b>
                <div style={{ opacity: .8, margin: '4px 0' }}>
                  将生成 <code>{batch}-{sel.stage}-{String(sel.seq + 1).padStart(4, '0')}</code>
                  ，parent 指向 <code>{sel.run_id}</code>，stage_seq 保持 {sel.stage_seq}
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
