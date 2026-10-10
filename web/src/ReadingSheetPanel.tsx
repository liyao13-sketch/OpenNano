import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useI18n } from './i18n'

/**
 * Reading sheet panel — 现场抄读四件（工单 `20261010-数据线-to-工具线-01`）在 OpenNano 里的入口。
 *
 * 对齐数据线的离线填表器（NAS `共享/25_现场记录/菜单抄读/填表器_机台菜单抄读.html` v12）：
 *   ① 导入填表器导出件（16 列；`结果`/`图片`/`LoopN 区间` 三类非读数行不进待填统计）
 *   ② 结果录入（受控量名 · method 词表 · 可信度默认未核实 · 单位自动带出 · 项目模板存取/导入导出）
 *   ③ 比较器（run 升序 · 参数×run · 差异标红 · 数值等价不算差异 · 空值不参与 · 结果并排 · 导出 CSV）
 *   ④ 图片管理（tag · 自定义命名规则 · 改名归档 · 索引随表导出）
 *   ⑤ 落库预览（**只读**：core 的写仍归数据线，走 解析 → 复核 → build_core）
 *
 * 界面语言：**全英文**（owner 2026-09-13 定）；core 术语（quantity / method / verification /
 * machine_step / run / recipe / 单位）一律不翻 —— 翻了对不上库。
 */

type Row = Record<string, string>
type Sheet = {
  path?: string; name?: string; head: string[]; rows: Row[]; meta: Row
  stats: { total: number; filled: number; pend: number; bad: number
           kinds: Record<string, number>; excluded_non_reading: number }
  matrix: { steps: string[]; fields: string[]; rows: (Row | null)[][] }
  kinds: Record<string, number>; col_ok: boolean; problems: string[]
  vocab: { quantities: string[]; methods: string[]; quantity_source: string; method_source: string }
  field_meta: { field: string; label: string; hint: string; is_bit: boolean }[]
}

const post = async (url: string, body: any) => {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || `${r.status}`)
  return d
}
const get = async (url: string) => {
  const r = await fetch(url); const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || `${r.status}`); return d
}
const fw = (w: number) => ({ flex: `0 0 ${w}px`, minWidth: w, width: w })

// core 术语与枚举：**界面必须原样显示，不许翻**（i18n 体检按本行标注豁免）。
const VERIFS = ['未核实', '已核实', '存疑']            // i18n-keep：verification 枚举（默认未核实＝不替记录升可信度）
const STEP_RESULT = '结果'                             // i18n-keep：measurement 来源行的 machine_step 取值
const STEP_IMAGE = '图片'                              // i18n-keep：同上（图片索引行）
const LOOP_RANGE_RE = /^Loop([12]) 区间$/              // i18n-keep：循环区间行（界面/配方设定，非读数）
const SHEET_PREFIX_RE = /^机台读数_/                    // i18n-keep：导出件文件名前缀（导出于离线填表器）

const SECTIONS = ['sheet', 'results', 'compare', 'images', 'export'] as const
type Section = typeof SECTIONS[number]

export default function ReadingSheetPanel() {
  const { t } = useI18n()
  const [sec, setSec] = useState<Section>('sheet')
  const [sheets, setSheets] = useState<any[]>([])
  const [cols, setCols] = useState<string[]>([])
  const [placeholders, setPlaceholders] = useState<string[]>([])
  const [defaultRule, setDefaultRule] = useState('{slot}_{run}_{tag}{seq}{ext}')
  const [path, setPath] = useState('')
  const [paste, setPaste] = useState('')
  const [sh, setSh] = useState<Sheet | null>(null)
  const [meta, setMeta] = useState<Row>({ slot: '', run: '', recipe: '', reader: '', read_at: '', core_run_id: '', sample_id: '' })
  const [rows, setRows] = useState<Row[]>([])
  const [results, setResults] = useState<Row[]>([])
  const [onlyPend, setOnlyPend] = useState(true)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  // compare
  const [picked, setPicked] = useState<string[]>([])
  const [cmp, setCmp] = useState<any>(null)
  const [cmpOnlyDiff, setCmpOnlyDiff] = useState(true)
  // results templates
  const [tpls, setTpls] = useState<Record<string, any[]>>({})
  const [tplName, setTplName] = useState('')
  // images
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [imgs, setImgs] = useState<Row[]>([])
  const [rule, setRule] = useState('')
  const [archive, setArchive] = useState<any>(null)
  // landing preview
  const [landing, setLanding] = useState<any>(null)

  const refreshList = useCallback(async () => {
    try {
      const d = await get('/api/sheet/list')
      setSheets(d.sheets || []); setCols(d.columns || [])
      setPlaceholders(d.placeholders || []); setDefaultRule(d.name_rule_default || '')
      setRule(r => r || d.name_rule_default || '')
    } catch (e: any) { setErr(e.message) }
  }, [])
  const refreshTpls = useCallback(async () => {
    try { setTpls((await get('/api/sheet/templates')).templates || {}) } catch { /* ignore */ }
  }, [])
  useEffect(() => { refreshList(); refreshTpls() }, [refreshList, refreshTpls])

  const applySheet = (d: Sheet, keepResults = false) => {
    setSh(d); setRows(d.rows || []); setMeta(m => ({
      ...m, recipe: d.meta.recipe || m.recipe, run: d.meta.run || m.run,
      reader: d.meta.reader || m.reader, read_at: d.meta.read_at || m.read_at,
      core_run_id: d.meta.core_run_id || m.core_run_id, sample_id: d.meta.sample_id || m.sample_id,
      slot: m.slot || (d.name || '').replace(SHEET_PREFIX_RE, '').replace(/[._].*$/, ''),
    }))
    if (!keepResults) setResults(d.rows.filter(r => r.machine_step === STEP_RESULT)
      .map(r => ({ name: r.field, value: r.machine_value, unit: r.unit, note: r.note,
                   quantity: r.quantity, method: r.method, verification: r.verification || VERIFS[0] })))
    setLanding(null); setArchive(null)
  }

  const load = async (p: string) => {
    setBusy('load'); setErr(''); setNote('')
    try { applySheet(await post('/api/sheet/parse', { path: p })) }
    catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }
  const loadPaste = async () => {
    if (!paste.trim()) return
    setBusy('load'); setErr('')
    try { applySheet(await post('/api/sheet/parse', { text: paste })) }
    catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }

  const doCompare = async () => {
    setBusy('cmp'); setErr('')
    try { setCmp(await post('/api/sheet/compare', { paths: picked })) }
    catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }
  const doPreview = async () => {
    setBusy('prev'); setErr('')
    try { setLanding(await post('/api/sheet/preview', { rows: [...rows, ...resultsToRows()] })) }
    catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }
  const downloadSheet = async () => {
    const r = await fetch('/api/sheet/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: meta, rows, ranges: loopRanges(), results, images: imgs }),
    })
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = 'reading_sheet.csv'; a.click()
  }
  const downloadCompare = () => {
    if (!cmp?.csv) return
    const blob = new Blob(['\ufeff' + cmp.csv], { type: 'text/csv' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
    a.download = 'compare.csv'; a.click()
  }

  const resultsToRows = () => results.filter(x => (x.name || '').trim()).map(x => ({
    machine_step: STEP_RESULT, field: x.name, parsed_value: '', machine_value: x.value || '',
    unit: x.unit || '', note: x.note || '', quantity: x.quantity || '',
    method: x.method || '', verification: x.verification || VERIFS[0],
    core_run_id: meta.core_run_id, sample_id: meta.sample_id,
  }))
  const loopRanges = () => {
    const out: Record<string, any> = {}
    for (const r of rows) {
      const m = LOOP_RANGE_RE.exec(String(r.field || ''))
      if (!m) continue
      const v = (r.machine_value || r.parsed_value || '').split('-')
      if (v.length === 2) out[m[1]] = { a: v[0].trim(), b: v[1].trim() }
    }
    return out
  }

  const patchRow = (i: number, k: string, v: string) => setRows(a => a.map((x, j) => (j === i ? { ...x, [k]: v } : x)))
  const patchResult = (i: number, k: string, v: string) => setResults(a => a.map((x, j) => (j === i ? { ...x, [k]: v } : x)))
  const guess = (q: string) => {
    const S: [string, string][] = [['_nm_min', 'nm/min'], ['_nm', 'nm'], ['_pct', '%'], ['_deg', '°'],
      ['_mpa', 'MPa'], ['_pa', 'Pa'], ['_c', '°C'], ['_w', 'W']]
    for (const [s, u] of S) if (q.endsWith(s)) return u
    return ''
  }
  const qset = useMemo(() => new Set(sh?.vocab?.quantities || []), [sh])
  const mset = useMemo(() => new Set(sh?.vocab?.methods || []), [sh])
  const shownRows = useMemo(() => rows.map((r, i) => ({ r, i })).filter(({ r }) => {
    if (!onlyPend) return true
    if ([STEP_RESULT, STEP_IMAGE].includes(String(r.machine_step))) return false
    if (/^Loop[12] /.test(String(r.field || ''))) return false
    return !String(r.machine_value || '').trim()
  }), [rows, onlyPend])

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy('up'); setErr('')
    try {
      const added: Row[] = []
      for (const f of Array.from(files)) {
        const b64 = await new Promise<string>((res, rej) => {
          const fr = new FileReader()
          fr.onload = () => res(String(fr.result).split(',')[1] || ''); fr.onerror = rej
          fr.readAsDataURL(f)
        })
        const d = await post('/api/sheet/upload', { state: meta, name: f.name, data_b64: b64 })
        added.push({ name: d.name, tag: '', size: String(d.size) })
      }
      setImgs(a => [...a, ...added]); setNote(t('rs.uploaded', { n: added.length }))
    } catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }
  const doRename = async () => {
    setBusy('ren'); setErr('')
    try {
      const d = await post('/api/sheet/rename', { state: meta, images: imgs, rule })
      setArchive(d)
      if (d.archived?.length) setImgs(a => a.map(x => {
        const hit = d.archived.find((y: any) => y.orig === x.name)
        return hit ? { ...x, final: hit.final } : x
      }))
    } catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: '8px 10px' }}>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 'var(--fs-md)' }}>{t('rs.title')}</b>
        {SECTIONS.map(s => (
          <button key={s} className={sec === s ? 'btn' : 'btn ghost'} onClick={() => setSec(s)}>
            {t(`rs.sec.${s}`)}
          </button>
        ))}
        <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{t('rs.tip')}</span>
      </div>

      {err && <div style={{ color: '#e0a0a0', marginTop: 6, whiteSpace: 'pre-wrap' }}>{err}</div>}
      {note && <div className="dim" style={{ marginTop: 6 }}>{note}</div>}

      {/* ── ① 抄读表 ─────────────────────────────────────────── */}
      {sec === 'sheet' && (
        <>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            <select value="" onChange={e => { setPath(e.target.value); load(e.target.value) }} style={fw(300)}>
              <option value="">{t('rs.pickSheet')}</option>
              {sheets.map(s => (
                <option key={s.path} value={s.path}>
                  {s.name} · {s.full16 ? '16c' : `${s.cols}c`}{s.frozen6_ok ? '' : ' ⚠'}
                </option>
              ))}
            </select>
            <input value={path} onChange={e => setPath(e.target.value)} placeholder={t('rs.pathPh')}
              style={{ flex: 1, minWidth: 240 }} />
            <button className="btn ghost" onClick={() => load(path)} disabled={!!busy}>{t('rs.open')}</button>
          </div>
          <div className="row" style={{ gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
            <input value={paste} onChange={e => setPaste(e.target.value)} placeholder={t('rs.pastePh')}
              style={{ flex: 1, minWidth: 300 }} />
            <button className="btn ghost" onClick={loadPaste} disabled={!!busy}>{t('rs.parsePaste')}</button>
          </div>

          {sh && (
            <>
              <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                <span className="chip">{sh.name}</span>
                <span className={sh.col_ok ? 'chip' : 'chip'} title={sh.problems.join(' / ')}>
                  {sh.col_ok ? t('rs.colOk') : t('rs.colBad')}
                </span>
                <span className="chip">{t('rs.statTotal', { n: sh.stats.total })}</span>
                <span className="chip">{t('rs.statFilled', { n: sh.stats.filled })}</span>
                <span className="chip">{t('rs.statPend', { n: sh.stats.pend })}</span>
                <span className="chip" style={{ color: sh.stats.bad ? '#e0a0a0' : undefined }}>
                  {t('rs.statBad', { n: sh.stats.bad })}
                </span>
                <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}
                  title={JSON.stringify(sh.kinds)}>
                  {t('rs.excluded', { n: sh.stats.excluded_non_reading })}
                </span>
              </div>
              {!sh.col_ok && sh.problems.map((p, i) => (
                <div key={i} className="dim" style={{ fontSize: 'var(--fs-sm)' }}>⚠ {p}</div>))}

              <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                {([['slot', 90], ['run', 70], ['recipe', 150], ['reader', 90], ['read_at', 150],
                  ['core_run_id', 190], ['sample_id', 110]] as [string, number][]).map(([k, w]) => (
                  <input key={k} value={meta[k] || ''} onChange={e => setMeta(m => ({ ...m, [k]: e.target.value }))}
                    placeholder={k} title={k} style={fw(w)} />
                ))}
                <label className="dim" style={{ fontSize: 'var(--fs-sm)' }}>
                  <input type="checkbox" checked={onlyPend} onChange={e => setOnlyPend(e.target.checked)} />
                  {' '}{t('rs.onlyPend')}
                </label>
              </div>

              <div style={{ marginTop: 6, maxHeight: 260, overflow: 'auto' }}>
                {shownRows.slice(0, 400).map(({ r, i }) => {
                  const kind = r.machine_step === STEP_RESULT ? 'result'
                    : r.machine_step === STEP_IMAGE ? 'image'
                      : /^Loop[12] /.test(String(r.field || '')) ? 'loop' : 'reading'
                  const state = !String(r.machine_value || '').trim() ? 'pend'
                    : !String(r.parsed_value || '').trim() ? 'noval'
                      : (String(r.machine_value).trim() === String(r.parsed_value).trim()
                        || Number(r.machine_value) === Number(r.parsed_value)) ? 'ok' : 'bad'
                  return (
                    <div className="row" key={i} style={{ gap: 4, flexWrap: 'wrap' }}>
                      <span className="dim" style={fw(56)}>{r.machine_step}</span>
                      <span style={fw(190)} title={r.field}>{r.field}</span>
                      <span className="dim" style={fw(74)}>{kind}</span>
                      <input value={r.machine_value || ''} onChange={e => patchRow(i, 'machine_value', e.target.value)}
                        placeholder={t('rs.valuePh')} style={fw(90)} />
                      <span className="dim" style={fw(74)} title={t('rs.parsedHint')}>{r.parsed_value}</span>
                      <span style={fw(46)}>
                        <span style={{ color: state === 'bad' ? '#e0a0a0' : state === 'ok' ? '#8fd18f' : undefined }}>
                          {state}
                        </span>
                      </span>
                      <input value={r.note || ''} onChange={e => patchRow(i, 'note', e.target.value)}
                        placeholder="note" style={{ flex: 1, minWidth: 90 }} />
                    </div>
                  )
                })}
              </div>
              <div className="dim" style={{ fontSize: 'var(--fs-sm)' }}>
                {t('rs.nonReadingNote')}
              </div>
            </>
          )}
        </>
      )}

      {/* ── ② 结果录入 ───────────────────────────────────────── */}
      {sec === 'results' && (
        <>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            <input value={tplName} onChange={e => setTplName(e.target.value)} placeholder={t('rs.tplNamePh')} style={fw(150)} />
            <button className="btn ghost" onClick={async () => {
              await post('/api/sheet/templates', { name: tplName, items: results }); refreshTpls()
              setNote(t('rs.tplSaved', { n: tplName }))
            }} disabled={!tplName}>{t('rs.tplSave')}</button>
            <select value="" onChange={e => {
              const items = tpls[e.target.value]
              if (items) setResults(a => items.map((x: Row) => {
                const old = a.find(y => y.name === x.name) || ({} as Row)
                return { ...x, value: old.value || '', note: old.note || '', verification: x.verification || VERIFS[0] }
              }))
            }} style={fw(150)}>
              <option value="">{t('rs.tplLoad')}</option>
              {Object.keys(tpls).map(k => <option key={k} value={k}>{k} ({tpls[k].length})</option>)}
            </select>
            <button className="btn ghost" onClick={async () => {
              await post('/api/sheet/templates/delete', { name: tplName }); refreshTpls()
            }} disabled={!tplName}>{t('rs.tplDelete')}</button>
            <button className="btn ghost" onClick={() => {
              const blob = new Blob([JSON.stringify(results, null, 1)], { type: 'application/json' })
              const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
              a.download = 'result_items.json'; a.click()
            }}>{t('rs.tplExport')}</button>
            <label className="btn ghost" style={{ cursor: 'pointer' }}>
              {t('rs.tplImport')}
              <input type="file" accept=".json" style={{ display: 'none' }} onChange={async e => {
                const f = e.target.files?.[0]; if (!f) return
                try {
                  const items = JSON.parse(await f.text())
                  setResults(a => (items as Row[]).map(x => {
                    const old = a.find(y => y.name === x.name) || ({} as Row)
                    return { ...x, value: old.value || '', verification: x.verification || VERIFS[0] }
                  }))
                } catch (err: any) { setErr(String(err.message || err)) }
              }} />
            </label>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{t('rs.qSource', { src: sh?.vocab?.quantity_source || '?' })}</span>
          </div>

          {results.map((x, i) => {
            const qBad = qset.size > 0 && x.quantity && !qset.has(x.quantity)
            const mBad = mset.size > 0 && x.method && !mset.has(x.method)
            return (
              <div className="row" key={i} style={{ gap: 4, marginTop: 4, flexWrap: 'wrap',
                borderLeft: qBad ? '3px solid #e0a0a0' : '3px solid transparent', paddingLeft: 4 }}>
                <input value={x.name || ''} onChange={e => patchResult(i, 'name', e.target.value)}
                  placeholder={t('rs.itemPh')} style={fw(150)} />
                <input value={x.value || ''} onChange={e => patchResult(i, 'value', e.target.value)}
                  placeholder="value" style={fw(74)} />
                <input value={x.unit || ''} onChange={e => patchResult(i, 'unit', e.target.value)}
                  placeholder="unit" style={fw(58)} />
                <input list="rs-q" value={x.quantity || ''} onChange={e => {
                  patchResult(i, 'quantity', e.target.value)
                  const g = guess(e.target.value); if (g) patchResult(i, 'unit', g)
                }} placeholder="quantity" style={fw(160)} />
                <datalist id="rs-q">{(sh?.vocab?.quantities || []).map(q => <option key={q} value={q} />)}</datalist>
                <input list="rs-m" value={x.method || ''} onChange={e => patchResult(i, 'method', e.target.value)}
                  placeholder="method" style={fw(120)} />
                <datalist id="rs-m">{(sh?.vocab?.methods || []).map(m => <option key={m} value={m} />)}</datalist>
                <select value={x.verification || VERIFS[0]} onChange={e => patchResult(i, 'verification', e.target.value)} style={fw(96)}>
                  {VERIFS.map(v => <option key={v} value={v}>{v}</option>)}
                </select>
                <input value={x.note || ''} onChange={e => patchResult(i, 'note', e.target.value)}
                  placeholder="note" style={{ flex: 1, minWidth: 90 }} />
                <button className="btn ghost" onClick={() => setResults(a => a.filter((_, j) => j !== i))}>×</button>
                {qBad && <span style={{ color: '#e0a0a0', fontSize: 'var(--fs-sm)' }}>{t('rs.qWarn')}</span>}
                {mBad && <span style={{ color: '#e0a0a0', fontSize: 'var(--fs-sm)' }}>{t('rs.mWarn')}</span>}
              </div>
            )
          })}
          <button className="btn ghost" onClick={() => setResults(a => [...a, {
            name: '', value: '', unit: '', note: '', quantity: '', method: '', verification: VERIFS[0],
          }])}>{t('rs.addItem')}</button>
          <div className="dim" style={{ fontSize: 'var(--fs-sm)', marginTop: 6 }}>{t('rs.resultsHint')}</div>
        </>
      )}

      {/* ── ③ 比较器 ─────────────────────────────────────────── */}
      {sec === 'compare' && (
        <>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            <select multiple value={picked} onChange={e => setPicked(Array.from(e.target.selectedOptions).map(o => o.value))}
              style={{ ...fw(420), height: 96 }}>
              {sheets.map(s => <option key={s.path} value={s.path}>{s.name}</option>)}
            </select>
            <button className="btn" onClick={doCompare} disabled={picked.length < 2 || !!busy}>{t('rs.compareRun')}</button>
            <label className="dim" style={{ fontSize: 'var(--fs-sm)' }}>
              <input type="checkbox" checked={cmpOnlyDiff} onChange={e => setCmpOnlyDiff(e.target.checked)} />
              {' '}{t('rs.onlyDiff')}
            </label>
            {cmp?.csv && <button className="btn ghost" onClick={downloadCompare}>{t('rs.compareExport')}</button>}
          </div>
          {cmp && (
            <>
              <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
                <span className="chip">{t('rs.runsN', { n: cmp.runs.length })}</span>
                <span className="chip" style={{ color: cmp.diff_count ? '#e0a0a0' : undefined }}>
                  {t('rs.diffN', { n: cmp.diff_count })}
                </span>
                <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{t('rs.orderNote')}</span>
                {cmp.unknown_run?.length > 0 && (
                  <span className="dim" style={{ fontSize: 'var(--fs-sm)', color: '#e0a0a0' }}>
                    {t('rs.noRunHint', { n: cmp.unknown_run.length })}
                  </span>)}
              </div>
              <div style={{ maxHeight: 300, overflow: 'auto', marginTop: 6 }}>
                <table className="tbl" style={{ width: '100%' }}>
                  <thead><tr><th>step</th><th>field</th>
                    {cmp.runs.map((r: any) => <th key={r.label}>{r.label}</th>)}</tr></thead>
                  <tbody>
                    {(cmpOnlyDiff ? cmp.params.filter((p: any) => p.differ) : cmp.params).slice(0, 300).map((p: any, i: number) => (
                      <tr key={i} style={{ background: p.differ ? 'rgba(224,160,160,.12)' : undefined }}>
                        <td>{p.step}</td><td title={p.label}>{p.field}</td>
                        {p.vals.map((v: string, j: number) => <td key={j}>{v}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {cmp.results.length > 0 && (
                <table className="tbl" style={{ width: '100%', marginTop: 8 }}>
                  <thead><tr><th>result item</th>
                    {cmp.runs.map((r: any) => <th key={r.label}>{r.label}</th>)}</tr></thead>
                  <tbody>
                    {cmp.results.map((p: any, i: number) => (
                      <tr key={i} style={{ background: p.differ ? 'rgba(224,160,160,.12)' : undefined }}>
                        <td>{p.name}</td>
                        {p.vals.map((v: string, j: number) => <td key={j}>{v}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="dim" style={{ fontSize: 'var(--fs-sm)', marginTop: 4 }}>{t('rs.cmpHint')}</div>
            </>
          )}
        </>
      )}

      {/* ── ④ 图片 ───────────────────────────────────────────── */}
      {sec === 'images' && (
        <>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            <button className="btn" onClick={() => fileRef.current?.click()} disabled={!!busy}>{t('rs.addImgs')}</button>
            <input ref={fileRef} type="file" accept="image/*" multiple style={{ display: 'none' }}
              onChange={e => onFiles(e.target.files)} />
            <input value={rule} onChange={e => setRule(e.target.value)} placeholder={t('rs.rulePh')} style={fw(300)} />
            <button className="btn ghost" onClick={() => setRule(defaultRule)}>{t('rs.ruleDefault')}</button>
            <button className="btn ghost" onClick={doRename} disabled={!imgs.length || !!busy}>{t('rs.renameArchive')}</button>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{t('rs.placeholders')}: {placeholders.map(p => `{${p}}`).join(' ')}</span>
          </div>
          {imgs.map((x, i) => (
            <div className="row" key={i} style={{ gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
              <span style={fw(220)} title={x.name}>{x.name}</span>
              <input value={x.tag || ''} onChange={e => setImgs(a => a.map((y, j) => (j === i ? { ...y, tag: e.target.value } : y)))}
                placeholder="tag" style={fw(110)} />
              <span className="dim" style={fw(260)}>{t('rs.finalName')}: {x.final || '—'}</span>
              <button className="btn ghost" onClick={() => setImgs(a => a.filter((_, j) => j !== i))}>×</button>
            </div>
          ))}
          {archive && (
            <div className="dim" style={{ fontSize: 'var(--fs-sm)', marginTop: 6 }}>
              {t('rs.archived', { n: archive.archived?.length || 0 })} → {archive.archive_dir}
              {archive.missing?.length ? ` · ${t('rs.missing', { n: archive.missing.length })}` : ''}
            </div>
          )}
          <div className="dim" style={{ fontSize: 'var(--fs-sm)', marginTop: 6 }}>{t('rs.imgHint')}</div>
        </>
      )}

      {/* ── ⑤ 导出 / 落库预览 ────────────────────────────────── */}
      {sec === 'export' && (
        <>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            <button className="btn" onClick={downloadSheet}>{t('rs.exportCsv')}</button>
            <button className="btn ghost" onClick={doPreview} disabled={!!busy}>{t('rs.previewRun')}</button>
            <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{t('rs.exportHint')}</span>
          </div>
          {landing && (
            <>
              <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
                <span className="chip">{t('rs.acceptedN', { n: landing.count })}</span>
                <span className="chip" style={{ color: landing.rejected?.length ? '#e0a0a0' : undefined }}>
                  {t('rs.rejectedN', { n: landing.rejected?.length || 0 })}
                </span>
                <span className="dim" style={{ fontSize: 'var(--fs-sm)' }}>{landing.note}</span>
              </div>
              {landing.accepted?.length > 0 && (
                <table className="tbl" style={{ width: '100%', marginTop: 6 }}>
                  <thead><tr><th>meas_id</th><th>quantity</th><th>value</th><th>unit</th>
                    <th>method</th><th>verification</th><th>note</th></tr></thead>
                  <tbody>{landing.accepted.map((a: any, i: number) => (
                    <tr key={i}><td>{a.meas_id}</td><td>{a.quantity}</td><td>{a.value}</td><td>{a.unit}</td>
                      <td>{a.method}</td><td>{a.verification}</td><td title={a.note}>{a.note?.slice(0, 40)}</td></tr>))}
                  </tbody>
                </table>
              )}
              {landing.rejected?.map((x: any, i: number) => (
                <div key={i} className="dim" style={{ fontSize: 'var(--fs-sm)' }}>⚠ {x.item}：{x.why}</div>))}
            </>
          )}
        </>
      )}
    </div>
  )
}
