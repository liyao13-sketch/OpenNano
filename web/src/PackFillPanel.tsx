import { useCallback, useEffect, useMemo, useState } from 'react'
import { useI18n } from './i18n'

/**
 * Pack fill panel — 在工具里直接填实验包的数据（不必导出 zip、改 CSV、再导入）。
 *
 * 为什么有这个面板（2026-10-06 owner 点名）：
 *   `expack` 的既有口径是「现场只允许在 `measurements.csv`/`observations.csv` 填数」——
 *   也就是**填数据必须离开工具**。本面板把那一步搬进来：选包 → 选 run → 填 → 保存（原地、原子）。
 *
 * 界面语言：**全英文**（owner 2026-09-13 定）。core 术语（quantity / obs_type / method /
 * verification / run_id / 单位）**一律不翻** —— 翻了对不上库。
 *
 * 与后端的分工（红线都在后端 `kb/pack_edit.py`，前端只做提示，不做最终裁决）：
 *   · 保存必须带 `revisions`（载入时的 sha256）⇒ 盘上被别人改过时后端回 409，界面提示"重新载入"；
 *   · 检测 run（stage ∈ METROLOGY_STAGES）**不许挂 measurement**（协议 §15.1）⇒ 行内先红字提示，
 *     真保存时后端也会拦（前端提示只是让人少走一趟）；
 *   · 只写 measurements / observations 两张表；别的表、别的列、run 的增删都不归这里。
 */
type Row = Record<string, string>

type RunInfo = {
  run_id: string; stage: string; sample_id: string; parent_run_id: string
  date: string; tool: string; tool_id: string; status: string
  is_metrology: boolean; has_measurements: boolean
}

type PackInfo = { path: string; batch_id: string; kind: string; writable: boolean; editable: boolean
  runs_n?: number; measurements_n?: number; observations_n?: number; source?: string; error?: string }

type Loaded = {
  path: string; kind: string; writable: boolean; batch_id: string
  columns: { measurements: string[]; observations: string[] }
  rows: { measurements: Row[]; observations: Row[] }
  runs: RunInfo[]
  revisions: Record<string, string>
  vocab: { quantities: string[]; obs_types: string[]; severities: string[]
           methods: string[]; verifications: string[]; source: string }
  rules: { detection_rule: string; no_inference: string; metrology_stages: string[] }
  warnings: string[]
}

const json = async (url: string, body?: any) => {
  const r = body === undefined
    ? await fetch(url)
    : await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || `${r.status}`)
  return d
}

export default function PackFillPanel() {
  const { t } = useI18n()
  const [packs, setPacks] = useState<PackInfo[]>([])
  const [roots, setRoots] = useState<string[]>([])
  const [path, setPath] = useState('')
  const [pack, setPack] = useState<Loaded | null>(null)
  const [meas, setMeas] = useState<Row[]>([])
  const [obs, setObs] = useState<Row[]>([])
  const [revs, setRevs] = useState<Record<string, string>>({})
  const [snapshot, setSnapshot] = useState('')
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [report, setReport] = useState<any>(null)

  const dirty = useMemo(
    () => !!pack && JSON.stringify({ m: meas, o: obs }) !== snapshot,
    [pack, meas, obs, snapshot])

  const refreshList = useCallback(async () => {
    try {
      const d = await json('/api/pack/list')
      setPacks(d.packs || []); setRoots(d.writable_roots || [])
    } catch (e: any) { setErr(e.message) }
  }, [])

  useEffect(() => { refreshList() }, [refreshList])

  const apply = (d: Loaded) => {
    setPack(d); setPath(d.path)
    setMeas(d.rows.measurements || []); setObs(d.rows.observations || [])
    setRevs(d.revisions || {})
    setSnapshot(JSON.stringify({ m: d.rows.measurements || [], o: d.rows.observations || [] }))
  }

  const load = useCallback(async (p: string) => {
    if (!p) return
    setBusy('load'); setErr(''); setNote('')
    try { apply(await json('/api/pack/load', { path: p })) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy('') }
  }, [])

  const save = async () => {
    if (!pack) return
    setBusy('save'); setErr(''); setNote('')
    try {
      const d = await json('/api/pack/save', {
        path: pack.path, measurements: meas, observations: obs, revisions: revs,
      })
      setReport(d); setNote(t('pack.saved', { n: (d.saved || []).length }))
      const fresh = await json('/api/pack/load', { path: pack.path })
      apply(fresh); setReport(d)
      refreshList()
    } catch (e: any) { setErr(e.message) }        // 409/400 的原文直接给用户（后端消息是自解释的）
    finally { setBusy('') }
  }

  const copyToWorkspace = async (p?: string) => {
    const target = p || path
    if (!target) return
    setBusy('copy'); setErr('')
    try {
      const d = await json('/api/pack/copy', { path: target })
      await refreshList(); await load(d.path)
      setNote(t('pack.copied', { to: d.path }))
    } catch (e: any) { setErr(e.message) }
    finally { setBusy('') }
  }

  const download = () => {
    if (!pack) return
    window.open(`/api/pack/download?path=${encodeURIComponent(pack.path)}`, '_blank')
  }

  const runOf = (rid: string) => pack?.runs.find(r => r.run_id === rid)
  const isMet = (rid: string) => !!runOf(rid)?.is_metrology
  const shown = (rows: Row[]) => filter ? rows.filter(r => r.run_id === filter) : rows
  const patch = (setter: (f: (a: Row[]) => Row[]) => void, i: number, k: string, v: string) =>
    setter(a => a.map((x, j) => (j === i ? { ...x, [k]: v } : x)))

  const runOptions = (pack?.runs || []).map(r =>
    <option key={r.run_id} value={r.run_id}>{r.run_id}{r.is_metrology ? ` ⚠ ${r.stage}` : ''}</option>)

  return (
    <div className="pack-fill" style={{ height: '100%', overflow: 'auto', padding: '8px 10px' }}>
      {/* ── 选包 ───────────────────────────────────────────────── */}
      <div className="row" style={{ gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 'var(--fs-md)' }}>{t('pack.title')}</b>
        <select value="" onChange={e => e.target.value && load(e.target.value)}
          style={{ minWidth: 230 }} title={t('pack.pickHint')}>
          <option value="">{t('pack.pickPlaceholder')}</option>
          {packs.map(p => (
            <option key={p.path} value={p.path}>
              {p.batch_id} · {p.editable ? t('pack.rw') : t('pack.ro')} · {p.runs_n ?? '?'} runs
            </option>
          ))}
        </select>
        <input value={path} onChange={e => setPath(e.target.value)} placeholder={t('pack.pathPh')}
          style={{ flex: 1, minWidth: 260 }} />
        <button className="btn ghost" onClick={() => load(path)} disabled={!!busy}>{t('pack.open')}</button>
        {pack && <button className="btn ghost" onClick={() => load(pack.path)} disabled={!!busy}>{t('pack.reload')}</button>}
        {pack && !pack.writable && (
          <button className="btn" onClick={() => copyToWorkspace()} disabled={!!busy}>{t('pack.copyToWorkspace')}</button>)}
        {pack?.writable && <button className="btn ghost" onClick={download}>{t('pack.download')}</button>}
      </div>

      {roots.length > 0 && (
        <div className="dim" style={{ fontSize: 'var(--fs-sm)', marginTop: 2 }}>
          {t('pack.writableRoots')} {roots.join(' · ')}
        </div>
      )}

      {!pack && <div className="dim" style={{ marginTop: 8 }}>{t('pack.emptyHint')}</div>}

      {pack && (
        <>
          {/* ── 包信息 ─────────────────────────────────────────── */}
          <div className="row" style={{ gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
            <span className="chip">{pack.batch_id}</span>
            <span className="chip">{pack.kind}</span>
            <span className="chip">{t('pack.runsN', { n: pack.runs.length })}</span>
            <span className="chip">{t('pack.measN', { n: meas.length })}</span>
            <span className="chip">{t('pack.obsN', { n: obs.length })}</span>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{pack.path}</span>
            {dirty && <span style={{ color: 'var(--c-warn, #e0a0a0)' }}>● {t('pack.unsaved')}</span>}
            <button className="btn" onClick={save} disabled={!pack.writable || !!busy || !dirty}
              title={pack.writable ? '' : t('pack.needCopy')}>
              {busy === 'save' ? t('pack.saving') : t('pack.save')}
            </button>
          </div>
          {!pack.writable && <div style={{ color: 'var(--c-warn, #e0a0a0)', marginTop: 2 }}>{t('pack.needCopy')}</div>}
          {pack.warnings.map((w, i) => <div key={i} className="dim" style={{ fontSize: 'var(--fs-sm)' }}>⚠ {w}</div>)}

          <div className="row" style={{ gap: 6, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
            <label className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{t('pack.filterRun')}</label>
            <select value={filter} onChange={e => setFilter(e.target.value)} style={{ minWidth: 200 }}>
              <option value="">{t('pack.allRuns')}</option>
              {runOptions}
            </select>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)', maxWidth: 620 }}>{pack.rules.detection_rule}</span>
          </div>

          {err && <div style={{ color: 'var(--c-warn, #e0a0a0)', marginTop: 6, whiteSpace: 'pre-wrap' }}>{err}</div>}
          {note && <div className="dim" style={{ marginTop: 6 }}>{note}</div>}
          {report && (
            <div className="dim" style={{ marginTop: 4, fontSize: 'var(--fs-sm)' }}>
              {t('pack.report')}{' '}
              {Object.entries(report.report || {}).map(([k, v]: any) =>
                `${k}: ${v.before}→${v.after}${v.dropped_empty ? ` (${t('pack.dropped', { n: v.dropped_empty })})` : ''}`).join(' · ')}
              {report.report?.measurements?.filled?.length
                ? ` · ${t('pack.autofilled')} ${report.report.measurements.filled.join('; ')}` : ''}
            </div>
          )}

          {/* ── measurements ──────────────────────────────────── */}
          <div style={{ marginTop: 10 }}>
            <b style={{ fontSize: 'var(--fs-md)' }}>{t('pack.measHead')}</b>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)', marginLeft: 8 }}>{t('pack.measHint')}</span>
            {shown(meas).map((r) => {
              const i = meas.indexOf(r)
              const bad = isMet(r.run_id)
              return (
                <div className="row" key={i} style={{ gap: 4, margin: '4px 0', flexWrap: 'wrap',
                  borderLeft: bad ? '3px solid #e0a0a0' : '3px solid transparent', paddingLeft: 4 }}>
                  <input value={r.meas_id || ''} onChange={e => patch(setMeas, i, 'meas_id', e.target.value)}
                    placeholder="meas_id…" style={{ width: 176 }} title={t('pack.autoId')} />
                  <select value={r.run_id || ''} onChange={e => patch(setMeas, i, 'run_id', e.target.value)} style={{ minWidth: 178 }}>
                    <option value="">{t('pack.pickRun')}</option>{runOptions}
                  </select>
                  <input list="pack-q" value={r.quantity || ''} onChange={e => patch(setMeas, i, 'quantity', e.target.value)}
                    placeholder="quantity" style={{ width: 150 }} />
                  <datalist id="pack-q">{pack.vocab.quantities.map(q => <option key={q} value={q} />)}</datalist>
                  <input value={r.value || ''} onChange={e => patch(setMeas, i, 'value', e.target.value)}
                    placeholder={t('pack.valuePh')} style={{ width: 74 }} />
                  <input value={r.unit || ''} onChange={e => patch(setMeas, i, 'unit', e.target.value)}
                    placeholder="unit" style={{ width: 54 }} />
                  <select value={r.method || ''} onChange={e => patch(setMeas, i, 'method', e.target.value)}>
                    <option value="">method</option>
                    {pack.vocab.methods.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <input value={r.loc || ''} onChange={e => patch(setMeas, i, 'loc', e.target.value)}
                    placeholder="loc" style={{ width: 58 }} />
                  <input value={r.n || ''} onChange={e => patch(setMeas, i, 'n', e.target.value)}
                    placeholder="n" style={{ width: 40 }} />
                  <input value={r.uncertainty || ''} onChange={e => patch(setMeas, i, 'uncertainty', e.target.value)}
                    placeholder="±" style={{ width: 52 }} />
                  <select value={r.verification || ''} onChange={e => patch(setMeas, i, 'verification', e.target.value)}>
                    <option value="">verification</option>
                    {pack.vocab.verifications.map(v => <option key={v} value={v}>{v}</option>)}
                  </select>
                  <input value={r.note || ''} onChange={e => patch(setMeas, i, 'note', e.target.value)}
                    placeholder="note" style={{ flex: 1, minWidth: 100 }} />
                  <button className="btn ghost" onClick={() => setMeas(a => a.filter((_, j) => j !== i))}>×</button>
                  {bad && <span style={{ color: '#e0a0a0', fontSize: 'var(--fs-sm)' }}>⚠ §15.1</span>}
                </div>
              )
            })}
            <button className="btn ghost" onClick={() => setMeas(a => [...a, {
              meas_id: '', run_id: filter || '', sample_id: '', quantity: '', value: '', unit: '',
              method: '', loc: '', n: '', uncertainty: '', verification: '', note: '',
            } as Row])}>{t('pack.addMeas')}</button>
          </div>

          {/* ── observations ──────────────────────────────────── */}
          <div style={{ marginTop: 10 }}>
            <b style={{ fontSize: 'var(--fs-md)' }}>{t('pack.obsHead')}</b>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)', marginLeft: 8 }}>{t('pack.obsHint')}</span>
            {shown(obs).map((r) => {
              const i = obs.indexOf(r)
              return (
                <div className="row" key={i} style={{ gap: 4, margin: '4px 0', flexWrap: 'wrap' }}>
                  <input value={r.obs_id || ''} onChange={e => patch(setObs, i, 'obs_id', e.target.value)}
                    placeholder="obs_id…" style={{ width: 176 }} title={t('pack.autoId')} />
                  <select value={r.run_id || ''} onChange={e => patch(setObs, i, 'run_id', e.target.value)} style={{ minWidth: 178 }}>
                    <option value="">{t('pack.pickRun')}</option>{runOptions}
                  </select>
                  <input list="pack-o" value={r.obs_type || ''} onChange={e => patch(setObs, i, 'obs_type', e.target.value)}
                    placeholder="obs_type" style={{ width: 168 }} />
                  <datalist id="pack-o">{pack.vocab.obs_types.map(o => <option key={o} value={o} />)}</datalist>
                  <select value={r.severity || ''} onChange={e => patch(setObs, i, 'severity', e.target.value)}>
                    <option value="">severity</option>
                    {pack.vocab.severities.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <input value={r.description || ''} onChange={e => patch(setObs, i, 'description', e.target.value)}
                    placeholder={t('pack.descPh')} style={{ flex: 1, minWidth: 200 }} />
                  <input value={r.recorded_by || ''} onChange={e => patch(setObs, i, 'recorded_by', e.target.value)}
                    placeholder="recorded_by" style={{ width: 110 }} />
                  <input value={r.date || ''} onChange={e => patch(setObs, i, 'date', e.target.value)}
                    placeholder="YYYY-MM-DD" style={{ width: 104 }} />
                  <button className="btn ghost" onClick={() => setObs(a => a.filter((_, j) => j !== i))}>×</button>
                </div>
              )
            })}
            <button className="btn ghost" onClick={() => setObs(a => [...a, {
              obs_id: '', run_id: filter || '', sample_id: '', obs_type: '', severity: '',
              description: '', judgement: '', action: '', recorded_by: '', date: '',
            } as Row])}>{t('pack.addObs')}</button>
          </div>

          <div className="dim" style={{ marginTop: 10, fontSize: 'var(--fs-sm)', opacity: .8 }}>
            {t('pack.footnote', { src: pack.vocab.source })}
          </div>
        </>
      )}
    </div>
  )
}
