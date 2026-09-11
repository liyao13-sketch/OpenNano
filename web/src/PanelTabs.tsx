import { useEffect, useState } from 'react'
import { api } from './api'
import type { Module } from './types'

export default function PanelTabs({ module, onUpdate }: {
  module: Module; onUpdate: (p: Partial<Module>) => void }) {
  const [tab, setTab] = useState<'params'|'doe'|'opt'|'sim'>('params')

  return (
    <div>
      <div style={{ display:'flex', borderBottom:'1px solid var(--border)', marginBottom:10 }}>
        {(['params','doe','opt','sim'] as const).map(t => (
          <div key={t} onClick={() => setTab(t)}
            style={{ padding:'6px 16px', cursor:'pointer', fontWeight: tab===t?700:400,
              color: tab===t?'var(--accent)':'var(--muted)', borderBottom: tab===t?'2px solid var(--accent)':'2px solid transparent' }}>
            {{params:'参数', doe:'DOE', opt:'Opt', sim:'Sim'}[t]}
          </div>
        ))}
      </div>
      {tab === 'params' && <ParamsTab module={module} onUpdate={onUpdate} />}
      {tab === 'doe' && <DoeTab module={module} onUpdate={onUpdate} />}
      {tab === 'opt' && <OptTab module={module} />}
      {tab === 'sim' && <SimTab module={module} />}
    </div>
  )
}

function ParamsTab({ module, onUpdate }: { module: Module; onUpdate: (p: Partial<Module>) => void }) {
  return (
    <>
      {Object.entries(module.param_defs).map(([k, d]) => (
        <div className="row" key={k}><label>{d.label}</label>
          <input type="number" step="any" value={module.params[k] ?? d.default}
            onChange={e => onUpdate({ params: { ...module.params, [k]: parseFloat(e.target.value) || 0 } })} />
        </div>
      ))}
      {Object.keys(module.param_defs).length === 0 && <div style={{ color:'var(--muted)' }}>（无参数定义）</div>}
    </>
  )
}

function DoeTab({ module, onUpdate }: { module: Module; onUpdate: (p: Partial<Module>) => void }) {
  const vars = Object.entries(module.param_defs).map(([k, d]) => ({
    key: k, label: d.label, min: d.min, max: d.max, def: d.default,
    checked: false, lo: d.min, hi: d.max, step: Math.max(1, (d.max - d.min) / 4),
  }))
  const [rows, setRows] = useState(vars)
  const [design, setDesign] = useState<'full'|'partial'|'bbd'|'ccd'>('full')
  const [randomize, setRandomize] = useState(true)
  const [matrix, setMatrix] = useState<number[][] | null>(null)

  const gen = async () => {
    const variables = rows.filter(r => r.checked).map(r => ({ param: r.key, min: r.lo, max: r.hi, step: r.step }))
    if (!variables.length) { setMatrix(null); return }
    const res = await api.doe({ variables, design_type: design, randomize })
    setMatrix(res.matrix)
    onUpdate({ doe: { variables, design_type: design, matrix: res.matrix, runs: res.runs, status: 'draft' } })
  }

  return (
    <div>
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:8 }}>勾选要扫描的参数,设 min/max/step。</div>
      {rows.map((r, i) => (
        <div className="row" key={r.key} style={{ fontSize:12 }}>
          <label style={{ width:110, textAlign:'left' }}><input type="checkbox" checked={r.checked}
            onChange={e => setRows(rs => rs.map((x, j) => j===i ? {...x, checked:e.target.checked} : x))} /> {r.label}</label>
          <input type="number" style={{width:56}} value={r.lo} onChange={e => setRows(rs => rs.map((x,j)=>j===i?{...x,lo:parseFloat(e.target.value)||0}:x))} />
          <input type="number" style={{width:56}} value={r.hi} onChange={e => setRows(rs => rs.map((x,j)=>j===i?{...x,hi:parseFloat(e.target.value)||0}:x))} />
          <input type="number" style={{width:56}} value={r.step} onChange={e => setRows(rs => rs.map((x,j)=>j===i?{...x,step:parseFloat(e.target.value)||1}:x))} />
        </div>
      ))}
      <div className="row" style={{ marginTop:8 }}>
        <select value={design} onChange={e => setDesign(e.target.value as any)}>
          <option value="full">全因子</option><option value="partial">部分因子</option>
          <option value="bbd">BBD(Box-Behnken)</option><option value="ccd">CCD(中心复合)</option>
        </select>
        <label style={{ width:'auto', fontSize:12, color:'var(--muted)', cursor:'pointer' }}>
          <input type="checkbox" checked={randomize} onChange={e => setRandomize(e.target.checked)} /> 随机化顺序
        </label>
        <button className="btn" onClick={gen}>生成矩阵</button>
      </div>
      {matrix && (
        <div style={{ marginTop:10, overflowX:'auto', maxHeight:220, overflowY:'auto', border:'1px solid var(--border)', borderRadius:8 }}>
          <table style={{ width:'100%', fontSize:11, borderCollapse:'collapse' }}>
            <thead><tr>{rows.filter(r=>r.checked).map(r => <th key={r.key} style={{ padding:4, borderBottom:'1px solid var(--border)' }}>{r.label}</th>)}</tr></thead>
            <tbody>{matrix.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} style={{ padding:4, textAlign:'center' }}>{v}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {matrix && <div style={{ color:'var(--muted)', fontSize:11, marginTop:4 }}>共 {matrix.length} runs</div>}
    </div>
  )
}

function SimTab({ module }: { module: Module }) {
  const outs = module.param_outputs.length ? module.param_outputs.join(' · ') : 'CD / 侧壁角 / 深度'
  return (
    <div>
      <div style={{ color:'var(--muted)', marginBottom:8 }}>仿真功能(占位):预测本步加工结果。</div>
      <button className="btn" disabled>运行仿真(占位)</button>
      <div style={{ marginTop:10, padding:12, background:'var(--surface)', borderRadius:8, color:'var(--muted)', fontSize:12, minHeight:80 }}>
        结果区: 将预测 {outs}<br/><span style={{fontSize:11}}>目标: 工艺协同优化 + ML 预测(后续接入模型)</span>
      </div>
    </div>
  )
}

const OPT_TARGETS = ['er_nm_min', 'depth_center_nm', 'selectivity', 'sidewall_angle_deg', 'final_cd_nm']

function OptTab({ module }: { module: Module }) {
  const [processType, setProcessType] = useState(module.subtype === 'etch' ? 'RIE_Cl' : 'RIE_Cl')
  const [material, setMaterial] = useState(module.material?.material || '')
  const [target, setTarget] = useState('depth_center_nm')
  const [fitRes, setFitRes] = useState<any>(null)
  const [mode, setMode] = useState<'max'|'min'|'target'>('max')
  const [tval, setTval] = useState(0)
  const [sug, setSug] = useState<any>(null)
  const [plotKind, setPlotKind] = useState<'contour'|'main'|null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [fields, setFields] = useState<{field:string;label:string;unit:string;count:number;param?:string|null}[]>([])

  // 结果字段动态列出(你表里有多少结果列,这里就有多少可选目标)
  useEffect(() => {   // 目标量来自数据域 core(权威源)
    api.coreQuantities()
      .then(r => setFields((r.quantities || []).map(q => ({
        field: q.quantity, label: q.quantity, unit: q.unit, count: q.n,
        param: null }))))
      .catch(() => setFields([]))
  }, [processType, material])

  const fit = async () => {
    setBusy(true); setErr(''); setSug(null); setPlotKind(null)
    try {
      const STAGE: Record<string,string> = { RIE_Cl: 'RIE', RIE_F: 'RIE', DRIE_Bosch: 'DRIE' }
      const r = await api.optFit({ source: 'core', quantity: target, stage: STAGE[processType] || null,
                                   tool_id: material || null, process_type: processType, target })
      setFitRes(r)
    } catch (e: any) { setErr('拟合失败: ' + e.message) } finally { setBusy(false) }
  }
  const suggest = async () => {
    if (!fitRes?.model_id) return
    setBusy(true); setErr('')
    try {
      const r = await api.optSuggest({ model_id: fitRes.model_id, mode, target_value: mode === 'target' ? tval : null, n: 5 })
      setSug(r)
    } catch (e: any) { setErr('建议失败: ' + e.message) } finally { setBusy(false) }
  }

  return (
    <div>
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:8 }}>
        用知识库实测数据拟合 GPR 代理模型(参数 → 目标),再由贝叶斯优化(EI)推荐下一轮实验。
      </div>
      <div className="row">
        <select value={processType} onChange={e => setProcessType(e.target.value)} style={{ width:110 }}>
          <option value="RIE_Cl">氯基 RIE</option><option value="RIE_F">氟基 RIE</option><option value="DRIE_Bosch">DRIE</option>
        </select>
        <input placeholder="机台(空=全部),如 RIE200NL" value={material} style={{ width:130 }}
          onChange={e => setMaterial(e.target.value)} />
        <select value={target} onChange={e => setTarget(e.target.value)}>
          {[...new Set([...fields.map(f => f.field), ...OPT_TARGETS])].map(t => {
            const meta = fields.find(f => f.field === t)
            return (
              <option key={t} value={t}>
                {meta ? `${meta.label}${meta.unit ? ` (${meta.unit})` : ''} · ${meta.count} 条${meta.param ? ` · ↦ ${meta.param}` : ''}` : t}
              </option>
            )
          })}
        </select>
      </div>
      <div className="row" style={{ marginTop:6 }}>
        <button className="btn" onClick={fit} disabled={busy}>{busy ? '…' : '拟合模型'}</button>
        {fitRes && (
          <>
            <select value={mode} onChange={e => setMode(e.target.value as any)} style={{ width:100 }}>
              <option value="max">最大化</option><option value="min">最小化</option><option value="target">逼近值</option>
            </select>
            {mode === 'target' && <input type="number" style={{ width:80 }} value={tval}
              onChange={e => setTval(parseFloat(e.target.value) || 0)} />}
            <button className="btn ghost" onClick={suggest} disabled={busy}>BO 建议</button>
          </>
        )}
      </div>
      {err && <div style={{ color:'#f87171', fontSize:12, marginTop:6 }}>{err}</div>}
      {fitRes && (
        <div style={{ marginTop:10, padding:10, background:'var(--surface)', borderRadius:8, fontSize:12 }}>
          <b>模型 {fitRes.model_id}</b> · {fitRes.n} 样本 · CV R²={String(fitRes.cv_r2?.toFixed(3))} · train R²={String(fitRes.train_r2?.toFixed(3))}
          {fitRes.fallback_linear && <span style={{ color:'#eab308' }}>（样本少,线性退化）</span>}
          <div style={{ color:'var(--muted)', marginTop:4 }}>特征: {fitRes.features.join(', ')}</div>
          <div className="row" style={{ marginTop:6 }}>
            <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }} onClick={() => setPlotKind('contour')}>响应面</button>
            <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }} onClick={() => setPlotKind('main')}>主效应</button>
          </div>
        </div>
      )}
      {fitRes && plotKind && (
        <img src={api.optPlotUrl(fitRes.model_id, plotKind)} alt="opt plot"
          style={{ width:'100%', marginTop:8, borderRadius:8, border:'1px solid var(--border)' }} />
      )}
      {sug && (
        <div style={{ marginTop:10 }}>
          <div style={{ fontSize:12, color:'var(--muted)', marginBottom:4 }}>
            下一轮建议 · {sug.strategy === 'space_filling' ? '空间填充探索' : 'EI 最大化'} · {sug.suggestions.length} 点
          </div>
          {sug.note && <div style={{ fontSize:11, color:'var(--warn)', marginBottom:6 }}>⚠ {sug.note}</div>}
          {sug.suggestions.map((s: any, i: number) => (
            <div key={i} style={{ border:'1px solid var(--border)', borderRadius:8, padding:'6px 10px', marginBottom:6, fontSize:12 }}>
              <div style={{ color:'var(--accent)', fontWeight:600 }}>#{i + 1} · 预测 {s.predicted}{s.std != null ? ` ±${s.std}` : ''}</div>
              <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:3 }}>
                {Object.entries(s.params).map(([k, v]) => (
                  <span key={k} className="chip" style={{ fontSize:11 }}>{k}={String(v)}</span>
                ))}
              </div>
              <div style={{ color:'var(--muted)', fontSize:11, marginTop:3 }}>{s.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
