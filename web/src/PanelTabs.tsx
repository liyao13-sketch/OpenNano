import { useEffect, useState } from 'react'
import { api } from './api'
import type { Module } from './types'
import { useI18n } from './i18n'

export default function PanelTabs({ module: raw, onUpdate }: {
  module: Module; onUpdate: (p: Partial<Module>) => void }) {
  const { t } = useI18n()
  const [tab, setTab] = useState<'params'|'doe'|'opt'|'sim'>('params')
  /* 字段归一：包/core/续做来的模块可能缺字段 ⇒ 任何子页签直接访问都会抛错并白屏 */
  const module: Module = {
    ...raw,
    params: raw.params || {}, param_defs: raw.param_defs || {},
    param_outputs: raw.param_outputs || [], param_inputs: raw.param_inputs || [],
    key_values: raw.key_values || {}, formulas: raw.formulas || {},
    material: raw.material || {}, annotations: raw.annotations || [],
  }

  return (
    <div>
      <div style={{ display:'flex', borderBottom:'1px solid var(--border)', marginBottom:10 }}>
        {(['params','doe','opt','sim'] as const).map(tabKey => (
          <div key={tabKey} onClick={() => setTab(tabKey)}
            style={{ padding:'6px 16px', cursor:'pointer', fontWeight: tab===tabKey?700:400,
              color: tab===tabKey?'var(--accent-text)':'var(--muted)', borderBottom: tab===tabKey?'2px solid var(--accent)':'2px solid transparent' }}>
            {{params: t('pt.params'), doe:'DOE', opt:'Opt', sim:'Sim'}[tabKey]}
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
  const { t } = useI18n()
  return (
    <>
      {Object.entries(module.param_defs).map(([k, d]) => (
        <div className="row" key={k}><label>{d.label}</label>
          <input type="number" step="any" value={module.params[k] ?? d.default}
            onChange={e => onUpdate({ params: { ...module.params, [k]: parseFloat(e.target.value) || 0 } })} />
        </div>
      ))}
      {Object.keys(module.param_defs).length === 0 && <div style={{ color:'var(--muted)' }}>{t('pt.noDefs')}</div>}
    </>
  )
}

function DoeTab({ module, onUpdate }: { module: Module; onUpdate: (p: Partial<Module>) => void }) {
  const { t } = useI18n()
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
      <div style={{ color:'var(--muted)', fontSize: 'var(--fs-base)', marginBottom:8 }}>{t('pt.doeHint')}</div>
      {rows.map((r, i) => (
        <div className="row" key={r.key} style={{ fontSize: 'var(--fs-base)' }}>
          <label style={{ width:110, textAlign:'left' }}><input type="checkbox" checked={r.checked}
            onChange={e => setRows(rs => rs.map((x, j) => j===i ? {...x, checked:e.target.checked} : x))} /> {r.label}</label>
          <input type="number" style={{width:56}} value={r.lo} onChange={e => setRows(rs => rs.map((x,j)=>j===i?{...x,lo:parseFloat(e.target.value)||0}:x))} />
          <input type="number" style={{width:56}} value={r.hi} onChange={e => setRows(rs => rs.map((x,j)=>j===i?{...x,hi:parseFloat(e.target.value)||0}:x))} />
          <input type="number" style={{width:56}} value={r.step} onChange={e => setRows(rs => rs.map((x,j)=>j===i?{...x,step:parseFloat(e.target.value)||1}:x))} />
        </div>
      ))}
      <div className="row" style={{ marginTop:8 }}>
        <select value={design} onChange={e => setDesign(e.target.value as any)}>
          <option value="full">{t('pt.full')}</option><option value="partial">{t('pt.partial')}</option>
          <option value="bbd">BBD(Box-Behnken)</option><option value="ccd">{t('pt.ccd')}</option>
        </select>
        <label style={{ width:'auto', fontSize: 'var(--fs-base)', color:'var(--muted)', cursor:'pointer' }}>
          <input type="checkbox" checked={randomize} onChange={e => setRandomize(e.target.checked)} /> {t('pt.randomize')}
        </label>
        <button className="btn" onClick={gen}>{t('pt.genMatrix')}</button>
      </div>
      {matrix && (
        <div style={{ marginTop:10, overflowX:'auto', maxHeight:220, overflowY:'auto', border:'1px solid var(--border)', borderRadius:8 }}>
          <table style={{ width:'100%', fontSize: 'var(--fs-xs)', borderCollapse:'collapse' }}>
            <thead><tr>{rows.filter(r=>r.checked).map(r => <th key={r.key} style={{ padding:4, borderBottom:'1px solid var(--border)' }}>{r.label}</th>)}</tr></thead>
            <tbody>{matrix.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} style={{ padding:4, textAlign:'center' }}>{v}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {matrix && <div style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)', marginTop:4 }}>共 {matrix.length} runs</div>}
    </div>
  )
}

function SimTab({ module }: { module: Module }) {
  const { t } = useI18n()
  const outs = module.param_outputs.length ? module.param_outputs.join(' · ') : t('pt.fallbackOuts')
  return (
    <div>
      <div style={{ color:'var(--muted)', marginBottom:8 }}>{t('pt.simHint')}</div>
      <button className="btn" disabled>{t('pt.runSim')}</button>
      <div style={{ marginTop:10, padding:12, background:'var(--surface)', borderRadius:8, color:'var(--muted)', fontSize: 'var(--fs-base)', minHeight:80 }}>
        结果区: {t('pt.simOuts', { outs })}<br/><span style={{fontSize: 'var(--fs-xs)'}}>{t('pt.simTarget')}</span>
      </div>
    </div>
  )
}

const OPT_TARGETS = ['er_nm_min', 'depth_center_nm', 'selectivity', 'sidewall_angle_deg', 'final_cd_nm']

function OptTab({ module }: { module: Module }) {
  const { t } = useI18n()
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
      <div style={{ color:'var(--muted)', fontSize: 'var(--fs-base)', marginBottom:8 }}>
        用知识库实测数据拟合 GPR 代理模型(参数 → 目标),再由贝叶斯优化(EI)推荐下一轮实验。
      </div>
      <div className="row">
        <select value={processType} onChange={e => setProcessType(e.target.value)} style={{ width:110 }}>
          <option value="RIE_Cl">{t('pt.clrie')}</option><option value="RIE_F">{t('pt.frie')}</option><option value="DRIE_Bosch">DRIE</option>
        </select>
        <input placeholder={t('pt.machine')} value={material} style={{ width:130 }}
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
        <button className="btn" onClick={fit} disabled={busy}>{busy ? '…' : t('pt.model')}</button>
        {fitRes && (
          <>
            <select value={mode} onChange={e => setMode(e.target.value as any)} style={{ width:100 }}>
              <option value="max">{t('pt.maximize')}</option><option value="min">{t('pt.minimize')}</option><option value="target">{t('pt.target')}</option>
            </select>
            {mode === 'target' && <input type="number" style={{ width:80 }} value={tval}
              onChange={e => setTval(parseFloat(e.target.value) || 0)} />}
            <button className="btn ghost" onClick={suggest} disabled={busy}>{t('pt.boSuggest')}</button>
          </>
        )}
      </div>
      {err && <div style={{ color:'var(--bad)', fontSize: 'var(--fs-base)', marginTop:6 }}>{err}</div>}
      {fitRes && (
        <div style={{ marginTop:10, padding:10, background:'var(--surface)', borderRadius:8, fontSize: 'var(--fs-base)' }}>
          <b>模型 {fitRes.model_id}</b> · {fitRes.n} 样本 · CV R²={String(fitRes.cv_r2?.toFixed(3))} · train R²={String(fitRes.train_r2?.toFixed(3))}
          {fitRes.fallback_linear && <span style={{ color:'var(--warn)' }}>{t('pt.smallSample')}</span>}
          <div style={{ color:'var(--muted)', marginTop:4 }}>特征: {fitRes.features.join(', ')}</div>
          <div className="row" style={{ marginTop:6 }}>
            <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }} onClick={() => setPlotKind('contour')}>{t('pt.surface')}</button>
            <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }} onClick={() => setPlotKind('main')}>{t('pt.mainEffect')}</button>
          </div>
        </div>
      )}
      {fitRes && plotKind && (
        <img src={api.optPlotUrl(fitRes.model_id, plotKind)} alt="opt plot"
          style={{ width:'100%', marginTop:8, borderRadius:8, border:'1px solid var(--border)' }} />
      )}
      {sug && (
        <div style={{ marginTop:10 }}>
          <div style={{ fontSize: 'var(--fs-base)', color:'var(--muted)', marginBottom:4 }}>
            Next suggestion · {sug.strategy === 'space_filling' ? t('pt.explore') : t('pt.ei')} · {t('pt.points', { n: sug.suggestions.length })}
          </div>
          {sug.note && <div style={{ fontSize: 'var(--fs-xs)', color:'var(--warn)', marginBottom:6 }}>⚠ {sug.note}</div>}
          {sug.suggestions.map((s: any, i: number) => (
            <div key={i} style={{ border:'1px solid var(--border)', borderRadius:8, padding:'6px 10px', marginBottom:6, fontSize: 'var(--fs-base)' }}>
              <div style={{ color:'var(--accent-text)', fontWeight:600 }}>#{i + 1} · 预测 {s.predicted}{s.std != null ? ` ±${s.std}` : ''}</div>
              <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:3 }}>
                {Object.entries(s.params).map(([k, v]) => (
                  <span key={k} className="chip" style={{ fontSize: 'var(--fs-xs)' }}>{k}={String(v)}</span>
                ))}
              </div>
              <div style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)', marginTop:3 }}>{s.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
