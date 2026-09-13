import { useEffect, useState } from 'react'
import { api } from './api'
import { useI18n } from './i18n'
import type { Library, Equipment, ParamDef } from './types'

export default function Settings({ onClose, onChange }: { onClose: () => void; onChange?: () => void }) {
  const { t: tr } = useI18n()
  const [lib, setLib] = useState<Library | null>(null)
  const [tab, setTab] = useState<'equipment'|'params'|'machines'|'defaults'|'rules'>('equipment')
  const [cat, setCat] = useState('etch')
  const [editEqId, setEditEqId] = useState<string | null>(null)
  const [newEq, setNewEq] = useState('')
  const [newParam, setNewParam] = useState({ name:'', unit:'', category:'尺寸' })   // i18n-keep：参数类别是库里的值（api.paramAdd 原样收）

  const refresh = () => api.library().then(setLib)
  useEffect(() => { refresh() }, [])

  const catLabel: Record<string,string> = {
    graphic:'图形化', etch:'刻蚀', deposition:'薄膜沉积', doping:'掺杂', bonding:'键合',   // i18n-keep：与后端 CATEGORY_LABELS 同一批值（见报告）
    packaging:'封装', wet:'湿法', thermal:'热处理', assist:'辅助',                          // i18n-keep：同上
  }

  if (!lib) return null
  const equipment = lib.equipment[cat] || []
  const eqNames = equipment.map(e => e.name)
  const defaults = lib.defaults?.equipment || {}

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(8,9,10,.72)', backdropFilter:'blur(2px)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:2000 }}>
      <div style={{ width:720, maxHeight:'82%', background:'var(--panel)', border:'1px solid var(--border)', borderRadius:14, display:'flex', flexDirection:'column', overflow:'hidden' }}>
        <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <b>{tr('set.title')}</b>
          <span style={{ cursor:'pointer', color:'var(--muted)' }} onClick={onClose}>✕</span>
        </div>
        <div style={{ display:'flex', borderBottom:'1px solid var(--border)' }}>
          {(['equipment','params','machines','defaults','rules'] as const).map(tabKey => (
            <div key={tabKey} onClick={() => setTab(tabKey)}
              style={{ padding:'8px 18px', cursor:'pointer', fontWeight: tab===tabKey ? 'var(--fw-bold)' : 'var(--fw-normal)',
                color: tab===tabKey ? 'var(--accent-text)' : 'var(--muted)', borderBottom: tab===tabKey ? '2px solid var(--accent)' : '2px solid transparent' }}>
              {{equipment: tr('set.tabEquipment'), params: tr('set.tabParams'), machines: tr('set.tabMachines'), defaults: tr('set.tabDefaults'), rules: tr('set.tabRules')}[tabKey]}
            </div>
          ))}
        </div>
        <div style={{ padding:16, overflowY:'auto', flex:1 }}>
          {tab === 'equipment' && (
            <>
              <select value={cat} onChange={e => { setCat(e.target.value); setEditEqId(null) }} style={{ padding:'6px 10px', background:'var(--surface)', color:'var(--text)', border:'1px solid var(--border)', borderRadius:6 }}>
                {Object.entries(catLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <div style={{ marginTop:12 }}>
                {equipment.map(eq => (
                  <div key={eq.id} style={{ marginBottom:6 }}>
                    <div className="row">
                      <span style={{ flex:1 }}>{eq.name}</span>
                      <span style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)' }}>{tr('set.eqMeta', { p: Object.keys(eq.params||{}).length, i: (eq.inputs||[]).length, o: (eq.outputs||[]).length })} · {(eq.inputs||[]).length}←/{(eq.outputs||[]).length}→</span>
                      <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }}
                        onClick={() => setEditEqId(editEqId === eq.id ? null : eq.id)}>{editEqId === eq.id ? tr('set.collapse') : tr('set.edit')}</button>
                      <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }} onClick={async () => { await api.eqRemove(eq.id); refresh() }}>{tr('set.delShort')}</button>
                    </div>
                    {editEqId === eq.id && <DeviceEditor key={eq.id + JSON.stringify(eq.params || {}).length} eq={eq} lib={lib} refresh={refresh} onClose={() => setEditEqId(null)} />}
                  </div>
                ))}
              </div>
              <div className="row" style={{ marginTop:12 }}>
                <input placeholder={tr('set.newEqPh')} value={newEq} onChange={e => setNewEq(e.target.value)} />
                <button className="btn" onClick={async () => { if(newEq.trim()){ await api.eqAdd(cat, newEq.trim()); setNewEq(''); refresh() } }}>{tr('set.add')}</button>
              </div>
            </>
          )}
          {tab === 'params' && (
            <ParamsTab lib={lib} refresh={refresh} />
          )}
          {tab === 'machines' && (
            <MachinesTab lib={lib} refresh={refresh} />
          )}
          {tab === 'rules' && <RulesTab lib={lib} refresh={refresh} />}
          {tab === 'defaults' && (
            <>
              {Object.entries(catLabel).map(([k, v]) => (
                <div className="row" key={k} style={{ marginBottom:8 }}>
                  <label style={{ width:90, textAlign:'left' }}>{v}</label>
                  <select value={defaults[k] || ''} onChange={async e => { await api.setDefault(k, e.target.value || null); refresh(); onChange?.() }}>
                    <option value="">{tr('set.builtin')}</option>
                    {(lib.equipment[k] || []).map(eq => <option key={eq.id} value={eq.id}>{eq.name}</option>)}
                  </select>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const PARAM_CAT_COLOR: Record<string,string> = { '尺寸':'var(--kind-process)', '膜厚':'var(--kind-inspect)', '材料':'var(--fam-resist)', '质量':'var(--fam-etch)' }   // i18n-keep：键是库里的参数类别值，翻了颜色就配不上

function RulesTab({ lib, refresh }: { lib: Library; refresh: () => void }) {
  const { t: tr } = useI18n()
  const params: Record<string, {unit?:string;category?:string}> = lib.params || {}
  const [rules, setRules] = useState<any[]>(lib.influence_rules || [])
  const suggestions = [...Object.keys(params), 'surface_film', 'gds_bias']

  const upd = (i: number, patch: Record<string, any>) =>
    setRules(rs => rs.map((r, j) => j === i ? { ...r, ...patch } : r))

  const input = (val: string, onChange: (v: string) => void, ph = '', w?: number) => (
    <input value={val} placeholder={ph} style={w ? { width: w } : undefined} onChange={e => onChange(e.target.value)} />
  )

  return (
    <div>
      <div style={{ color:'var(--muted)', fontSize: 'var(--fs-base)', marginBottom:10, lineHeight:1.7 }}>
        {tr('set.rulesIntro1')}<b style={{color:'var(--text)'}}>{tr('set.rulesIntroSrc')} → {tr('set.rulesIntroDst')}</b> + <b style={{color:'var(--text)'}}>{tr('set.rulesIntroWhen')}</b>{tr('set.rulesIntro2')}
        <code> surface_film == 'SiO₂' </code>{tr('set.rulesIntro3')}<code>expr</code>{tr('set.rulesIntro4')}
        <code> sign / mechanism </code>{tr('set.rulesIntro5')}
      </div>
      <datalist id="rule-params">{suggestions.map(s => <option key={s} value={s} />)}</datalist>
      {rules.map((r, i) => (
        <div key={r.id || i} style={{ border:'1px solid var(--border)', borderRadius:10, padding:'8px 10px', marginBottom:8, background:'var(--surface)' }}>
          <div className="row">
            <input list="rule-params" value={r.from || ''} placeholder={tr('set.fromPh')} onChange={e => upd(i, { from: e.target.value })} />
            <span style={{ color:'var(--accent-text)' }}>→</span>
            <input list="rule-params" value={r.to || ''} placeholder={tr('set.toPh')} onChange={e => upd(i, { to: e.target.value })} />
            <select value={r.sign || ''} onChange={e => upd(i, { sign: e.target.value })} style={{ width:86 }}>
              <option value="">{tr('set.qualPh')}</option><option value="+">{tr('set.pos')}</option>
              <option value="-">{tr('set.neg')}</option><option value="~">{tr('set.nonmono')}</option>
            </select>
            <select value={r.reliability_score ?? 3} onChange={e => upd(i, { reliability_score: parseInt(e.target.value) })} style={{ width:70 }}>
              {[1,2,3,4,5].map(n => <option key={n} value={n}>{n}/5</option>)}
            </select>
            <span style={{ cursor:'pointer', color:'var(--muted)' }} title={tr('set.delRule')}
              onClick={() => setRules(rs => rs.filter((_, j) => j !== i))}>✕</span>
          </div>
          <div className="row">
            {input(r.when || '', v => upd(i, { when: v }), tr('set.whenPh'))}
            {input(r.expr || '', v => upd(i, { expr: v }), tr('set.exprPh'))}
          </div>
          <div className="row">
            {input(r.mechanism || '', v => upd(i, { mechanism: v }), tr('set.mechPh'))}
            {input(r.source || '', v => upd(i, { source: v }), tr('set.srcPh'), 130)}
          </div>
        </div>
      ))}
      <div className="row" style={{ marginTop:4 }}>
        <button className="btn ghost" onClick={() => setRules(rs => [...rs, {
          id: `ir-${Date.now().toString(36)}`, from: '', to: '', when: '', expr: '',
          sign: '', mechanism: '', scope: 'global', source: '', reliability_score: 3, enabled: true,
        }])}>{tr('set.newRule')}</button>
        <button className="btn" onClick={async () => { await api.rulesSave(rules); refresh() }}>{tr('set.saveRules', { n: rules.length })}</button>
      </div>
    </div>
  )
}

/* ===== 设备编辑器:参数模板 + 承接/影响接口 + 公式模板 ===== */
function DeviceEditor({ eq, lib, refresh, onClose }: {
  eq: Equipment; lib: Library; refresh: () => void; onClose: () => void }) {
  const { t: tr } = useI18n()
  const [name, setName] = useState(eq.name)
  const [rows, setRows] = useState<[string, ParamDef][]>(() =>
    Object.entries(eq.params || {}).map(([k, d]) => [k, {
      label: d.label ?? k, unit: d.unit ?? '', default: d.default ?? 0,
      min: d.min ?? 0, max: d.max ?? 0,
    }]))
  const [inputs, setInputs] = useState<string[]>(eq.inputs || [])
  const [outputs, setOutputs] = useState<string[]>(eq.outputs || [])
  const [formulas, setFormulas] = useState<[string, string][]>(Object.entries(eq.formulas || {}))
  const [busy, setBusy] = useState(false)
  const paramNames = Object.keys(lib.params || {})

  const setRow = (i: number, patch: Partial<ParamDef>) =>
    setRows(rs => rs.map(([k, d], j) => j === i ? [k, { ...d, ...patch }] as [string, ParamDef] : [k, d] as [string, ParamDef]))

  const save = async () => {
    setBusy(true)
    try {
      const params: Record<string, ParamDef> = {}
      rows.forEach(([k, d]) => { if (k.trim()) params[k.trim()] = d })
      await api.eqUpdate(eq.id, {
        name: name.trim() || undefined, params, inputs, outputs,
        formulas: Object.fromEntries(formulas.filter(([o]) => o.trim())),
      })
      refresh(); onClose()
    } finally { setBusy(false) }
  }

  const num = (v: number, on: (n: number) => void, w = 58) => (
    <input type="number" step="any" style={{ width: w }} value={v}
      onChange={e => on(parseFloat(e.target.value) || 0)} />
  )

  return (
    <div style={{ border:'1px solid var(--accent)', borderRadius:10, padding:10, margin:'6px 0 10px', background:'var(--surface)' }}>
      <div className="row">
        <label style={{ width:60 }}>{tr('set.name')}</label>
        <input value={name} onChange={e => setName(e.target.value)} />
      </div>

      <div className="iface-sec">{tr('set.tplHead')}</div>
      <div className="row" style={{ fontSize: 'var(--fs-micro)', color:'var(--muted)' }}>
        <span style={{ width:110 }}>{tr('set.paramKey')}</span><span style={{ width:110 }}>{tr('set.label')}</span>
        <span style={{ width:48 }}>{tr('set.unit')}</span><span style={{ width:58 }}>{tr('set.default')}</span>
        <span style={{ width:58 }}>min</span><span style={{ width:58 }}>max</span>
      </div>
      {rows.map(([k, d], i) => (
        <div className="row" key={i} style={{ fontSize: 'var(--fs-xs)' }}>
          <input style={{ width:110 }} value={k} placeholder="key"
            onChange={e => setRows(rs => rs.map((x, j) => j === i ? [e.target.value, x[1]] : x))} />
          <input style={{ width:110 }} value={d.label} placeholder="label"
            onChange={e => setRow(i, { label: e.target.value })} />
          <input style={{ width:48 }} value={d.unit} onChange={e => setRow(i, { unit: e.target.value })} />
          {num(d.default, v => setRow(i, { default: v }))}
          {num(d.min, v => setRow(i, { min: v }))}
          {num(d.max, v => setRow(i, { max: v }))}
          <span className="chip-x" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}>✕</span>
        </div>
      ))}
      <div className="row">
        <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }}
          onClick={() => setRows(rs => [...rs, ['', { label:'', unit:'', default:0, min:0, max:0 }]])}>{tr('set.addParam')}</button>
      </div>

      <div className="iface-sec">{tr('set.ioHead')}</div>
      {(['in', 'out'] as const).map(kind => {
        const list = kind === 'in' ? inputs : outputs
        const set = kind === 'in' ? setInputs : setOutputs
        return (
          <div className="row" key={kind} style={{ flexWrap:'wrap' }}>
            <span style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)', width:26 }}>{kind === 'in' ? '←' : '→'}</span>
            {list.map(x => (
              <span key={x} className="chip" style={{ fontSize: 'var(--fs-xs)' }}>{x}
                <span className="chip-x" onClick={() => set(list.filter(y => y !== x))}>✕</span></span>
            ))}
            <select value="" style={{ width:130 }} onChange={e => { if (e.target.value) set([...list, e.target.value]) }}>
              <option value="">{tr('set.addMore')}</option>
              {paramNames.filter(p => !list.includes(p)).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        )
      })}

      <div className="iface-sec">{tr('set.formulaHead')}</div>
      {formulas.map(([out, expr], i) => (
        <div className="row" key={i}>
          <select style={{ width:110 }} value={out}
            onChange={e => setFormulas(fs => fs.map((f, j) => j === i ? [e.target.value, f[1]] : f))}>
            <option value="">{tr('set.pickOut')}</option>
            {[...new Set([...outputs, ...formulas.map(f => f[0])])].filter(Boolean)
              .map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input value={expr} placeholder={tr('set.formulaPh')}
            onChange={e => setFormulas(fs => fs.map((f, j) => j === i ? [f[0], e.target.value] : f))} />
          <span className="chip-x" onClick={() => setFormulas(fs => fs.filter((_, j) => j !== i))}>✕</span>
        </div>
      ))}
      <div className="row">
        <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }}
          onClick={() => setFormulas(fs => [...fs, ['', '']])}>{tr('set.addFormula')}</button>
      </div>

      <div className="row" style={{ marginTop:8 }}>
        <button className="btn" onClick={save} disabled={busy}>{busy ? '…' : tr('set.saveTpl')}</button>
        <button className="btn ghost" onClick={onClose}>{tr('set.collapse')}</button>
      </div>
    </div>
  )
}

/* ===== 参数:作用域(全局/工艺/机台) + 语义类别(可自定义) ===== */
function ParamsTab({ lib, refresh }: { lib: Library; refresh: () => void }) {
  const { t: tr } = useI18n()
  const params: Record<string, any> = lib.params || {}
  const cats: string[] = (lib as any).param_categories || ['尺寸', '膜厚', '材料', '质量']   // i18n-keep：库里的类别值（后端 param_categories 的默认值）
  const machines: any[] = (lib as any).machines || []
  const templates: { id: string; name: string }[] = Object.values(lib.equipment || {})
    .flat().map((e: any) => ({ id: e.id, name: e.name }))

  const [form, setForm] = useState({ name: '', unit: '', category: cats[0] || '尺寸', scope: 'global' })   // i18n-keep：兜底类别值是库里的值
  const [newCat, setNewCat] = useState('')

  const scopeLabel = (sc: string) => {
    if (!sc || sc === 'global') return tr('set.global')
    const [kind, id] = sc.split(':')
    if (kind === 'process') return tr('set.scopeProcess', { name: templates.find(t => t.id === id)?.name || id })
    if (kind === 'machine') return tr('set.scopeMachine', { name: machines.find((m: any) => m.id === id)?.name || id })
    return sc
  }
  const groups: Record<string, [string, any][]> = {}
  Object.entries(params).forEach(([k, v]: any) => {
    const sc = v.scope || 'global'
    ;(groups[sc] = groups[sc] || []).push([k, v])
  })

  return (
    <div>
      <div style={{ color:'var(--muted)', fontSize: 'var(--fs-base)', marginBottom:10, lineHeight:1.7 }}>
        {tr('set.paramsIntro1')}<b style={{color:'var(--text)'}}>{tr('set.paramsIntro2')}</b>{tr('set.paramsIntro3')}
        <b style={{color:'var(--text)'}}>{tr('set.paramsIntro4')}</b>{tr('set.paramsIntro5', { cats: cats.join(' / ') })}
      </div>

      <div className="row">
        <input placeholder={tr('set.paramNamePh')} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        <input placeholder={tr('set.unitPh')} style={{ width:64 }} value={form.unit} onChange={e => setForm({ ...form, unit: e.target.value })} />
        <select style={{ width:92 }} value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>
          {cats.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="row">
        <select value={form.scope} onChange={e => setForm({ ...form, scope: e.target.value })}>
          <option value="global">{tr('set.scopeGlobal')}</option>
          <optgroup label={tr('set.scopeProcessGroup')}>
            {templates.map(t => <option key={t.id} value={`process:${t.id}`}>{tr('set.scopeProcess', { name: t.name })}</option>)}
          </optgroup>
          <optgroup label={tr('set.scopeMachineGroup')}>
            {machines.map((m: any) => <option key={m.id} value={`machine:${m.id}`}>{tr('set.scopeMachine', { name: m.name })}</option>)}
          </optgroup>
        </select>
        <button className="btn" onClick={async () => {
          if (!form.name.trim()) return
          await api.paramAdd(form.name.trim(), form.unit, form.category, form.scope)
          setForm({ name: '', unit: '', category: form.category, scope: form.scope })
          refresh()
        }}>{tr('set.addParamBtn')}</button>
      </div>

      <div className="iface-sec">{tr('set.catHead')}</div>
      <div className="row" style={{ flexWrap:'wrap' }}>
        {cats.map(c => (
          <span key={c} className="chip" style={{ fontSize: 'var(--fs-xs)' }}>{c}
            <span className="chip-x" onClick={async () => { await api.categoryRemove(c); refresh() }}>✕</span></span>
        ))}
        <input placeholder={tr('set.newCatPh')} style={{ width:110 }} value={newCat} onChange={e => setNewCat(e.target.value)} />
        <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }}
          onClick={async () => { if (newCat.trim()) { await api.categoryAdd(newCat.trim()); setNewCat(''); refresh() } }}>{tr('set.addCat')}</button>
      </div>

      {Object.entries(groups).map(([sc, list]) => (
        <div key={sc}>
          <div className="iface-sec">{scopeLabel(sc)} ({list.length})</div>
          <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
            {list.map(([k, v]) => (
              <span key={k} className="chip" style={{ fontSize: 'var(--fs-sm)' }}
                title={`${scopeLabel(sc)} · ${v.category || ''} ${v.unit || ''}`}>
                <span style={{ width:6, height:6, borderRadius:2, background: PARAM_CAT_COLOR[v.category] || 'var(--faint)' }} />
                {k} <span style={{ color:'var(--muted)' }}>{v.unit}</span>
                <span className="chip-x" onClick={async () => { await api.paramRemove(k); refresh() }}>✕</span>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ===== 机台:型号/编号/别名/位置/状态/备注,挂在工艺模板下 ===== */
function MachinesTab({ lib, refresh }: { lib: Library; refresh: () => void }) {
  const { t: tr } = useI18n()
  const machines: any[] = (lib as any).machines || []
  const templates: { id: string; name: string }[] = Object.values(lib.equipment || {})
    .flat().map((e: any) => ({ id: e.id, name: e.name }))
  const [editId, setEditId] = useState<string | null>(null)
  const [form, setForm] = useState({ name: '', model: '', vendor: '', serial: '', equipment_id: '', location: '', status: 'active', max_sample: '', notes: '' })

  const blank = { name: '', model: '', vendor: '', serial: '', equipment_id: '', location: '', status: 'active', max_sample: '', notes: '' }

  const save = async () => {
    if (!form.name.trim()) return
    if (editId) await api.machineUpdate(editId, form)
    else await api.machineAdd(form)
    setForm(blank); setEditId(null); refresh()
  }

  return (
    <div>
      <div style={{ color:'var(--muted)', fontSize: 'var(--fs-base)', marginBottom:10, lineHeight:1.7 }}>
        {tr('set.machineIntro1')}<b style={{color:'var(--text)'}}>{tr('set.machineIntro2')}</b>{tr('set.machineIntro3')}
        {tr('set.machineIntro4')}
      </div>

      <div style={{ border:'1px solid var(--border)', borderRadius:10, padding:10, background:'var(--surface)', marginBottom:12 }}>
        <div className="row">
          <input placeholder={tr('set.machineAliasPh')} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input placeholder={tr('set.machineModelPh')} value={form.model} onChange={e => setForm({ ...form, model: e.target.value })} />
        </div>
        <div className="row">
          <input placeholder={tr('set.machineVendorPh')} value={form.vendor} onChange={e => setForm({ ...form, vendor: e.target.value })} />
          <input placeholder={tr('set.machineMaxPh')} style={{ width:110 }} value={form.max_sample} onChange={e => setForm({ ...form, max_sample: e.target.value })} />
        </div>
        <div className="row">
          <input placeholder={tr('set.machineSerialPh')} style={{ width:130 }} value={form.serial} onChange={e => setForm({ ...form, serial: e.target.value })} />
          <select value={form.equipment_id} onChange={e => setForm({ ...form, equipment_id: e.target.value })}>
            <option value="">{tr('set.machineTplPh')}</option>
            {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
        <div className="row">
          <input placeholder={tr('set.locPh')} style={{ width:110 }} value={form.location} onChange={e => setForm({ ...form, location: e.target.value })} />
          <select style={{ width:110 }} value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>
            <option value="active">{tr('set.stActive')}</option><option value="maintenance">{tr('set.stMaintenance')}</option>
            <option value="down">{tr('set.stDown')}</option><option value="retired">{tr('set.stRetired')}</option>
            <option value="待确认">待确认</option>{/* i18n-keep：机台状态枚举值，原样写回后端（form.status） */}
          </select>
          <input placeholder={tr('set.notesPh')} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} />
        </div>
        <div className="row">
          <button className="btn" onClick={save}>{editId ? tr('set.saveEdit') : tr('set.addMachine')}</button>
          {editId && <button className="btn ghost" onClick={() => { setEditId(null); setForm(blank) }}>{tr('set.cancel')}</button>}
        </div>
      </div>

      {machines.map((m: any) => (
        <div key={m.id} className="row" style={{ padding:'7px 10px', border:'1px solid var(--border)', borderRadius:8, marginBottom:6 }}>
          <span style={{ flex:1 }}>
            <b style={{ fontSize: 'var(--fs-md)' }}>{m.name}</b>
            <span style={{ color:'var(--muted)', fontSize: 'var(--fs-xs)' }}>
              {m.model ? ` · ${m.model}` : ''}{m.vendor ? ` · ${m.vendor}` : ''}{m.serial ? ` · #${m.serial}` : ''}{m.max_sample ? ` · ≤${m.max_sample}` : ''}
              {m.equipment_id ? ` · ${tr('set.tplOf', { name: templates.find(t => t.id === m.equipment_id)?.name || '?' })}` : ` · ${tr('set.noTpl')}`}
              {m.location ? ` · ${m.location}` : ''}{m.notes ? ` · ${m.notes}` : ''}
            </span>
          </span>
          <button className="btn ghost" style={{ fontSize: 'var(--fs-xs)', padding:'3px 10px' }}
            onClick={() => { setEditId(m.id); setForm({ name: m.name, model: m.model || '', vendor: m.vendor || '', serial: m.serial || '', equipment_id: m.equipment_id || '', location: m.location || '', status: m.status || 'active', max_sample: m.max_sample || '', notes: m.notes || '' }) }}>{tr('set.edit')}</button>
          <span className="chip-x" onClick={async () => { await api.machineRemove(m.id); refresh() }}>✕</span>
        </div>
      ))}
      {machines.length === 0 && <div style={{ color:'var(--muted)', fontSize: 'var(--fs-base)' }}>{tr('set.noMachines')}</div>}
    </div>
  )
}
