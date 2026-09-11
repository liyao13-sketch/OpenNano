import { useEffect, useState } from 'react'
import { api } from './api'
import type { Library, Equipment, ParamDef } from './types'

export default function Settings({ onClose, onChange }: { onClose: () => void; onChange?: () => void }) {
  const [lib, setLib] = useState<Library | null>(null)
  const [tab, setTab] = useState<'equipment'|'params'|'machines'|'defaults'|'rules'>('equipment')
  const [cat, setCat] = useState('etch')
  const [editEqId, setEditEqId] = useState<string | null>(null)
  const [newEq, setNewEq] = useState('')
  const [newParam, setNewParam] = useState({ name:'', unit:'', category:'尺寸' })

  const refresh = () => api.library().then(setLib)
  useEffect(() => { refresh() }, [])

  const catLabel: Record<string,string> = {
    graphic:'图形化', etch:'刻蚀', deposition:'薄膜沉积', doping:'掺杂', bonding:'键合',
    packaging:'封装', wet:'湿法', thermal:'热处理', assist:'辅助',
  }

  if (!lib) return null
  const equipment = lib.equipment[cat] || []
  const eqNames = equipment.map(e => e.name)
  const defaults = lib.defaults?.equipment || {}

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(8,9,10,.72)', backdropFilter:'blur(2px)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:2000 }}>
      <div style={{ width:720, maxHeight:'82%', background:'var(--panel)', border:'1px solid var(--border)', borderRadius:14, display:'flex', flexDirection:'column', overflow:'hidden' }}>
        <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <b>设置 · 库</b>
          <span style={{ cursor:'pointer', color:'var(--muted)' }} onClick={onClose}>✕</span>
        </div>
        <div style={{ display:'flex', borderBottom:'1px solid var(--border)' }}>
          {(['equipment','params','machines','defaults','rules'] as const).map(t => (
            <div key={t} onClick={() => setTab(t)}
              style={{ padding:'8px 18px', cursor:'pointer', fontWeight: tab===t ? 700 : 400,
                color: tab===t ? 'var(--accent)' : 'var(--muted)', borderBottom: tab===t ? '2px solid var(--accent)' : '2px solid transparent' }}>
              {{equipment:'工艺模板',params:'参数',machines:'机台',defaults:'默认',rules:'影响规则'}[t]}
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
                      <span style={{ color:'var(--muted)', fontSize:11 }}>{Object.keys(eq.params||{}).length} 参数 · {(eq.inputs||[]).length}←/{(eq.outputs||[]).length}→</span>
                      <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }}
                        onClick={() => setEditEqId(editEqId === eq.id ? null : eq.id)}>{editEqId === eq.id ? '收起' : '编辑'}</button>
                      <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }} onClick={async () => { await api.eqRemove(eq.id); refresh() }}>删</button>
                    </div>
                    {editEqId === eq.id && <DeviceEditor key={eq.id + JSON.stringify(eq.params || {}).length} eq={eq} lib={lib} refresh={refresh} onClose={() => setEditEqId(null)} />}
                  </div>
                ))}
              </div>
              <div className="row" style={{ marginTop:12 }}>
                <input placeholder="新设备名(默认参数模板,添加后可编辑)" value={newEq} onChange={e => setNewEq(e.target.value)} />
                <button className="btn" onClick={async () => { if(newEq.trim()){ await api.eqAdd(cat, newEq.trim()); setNewEq(''); refresh() } }}>添加</button>
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
                    <option value="">— 内置默认 —</option>
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

const PARAM_CAT_COLOR: Record<string,string> = { '尺寸':'#5e6ad2', '膜厚':'#a78bfa', '材料':'#d4a24e', '质量':'#e5645c' }

function RulesTab({ lib, refresh }: { lib: Library; refresh: () => void }) {
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
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:10, lineHeight:1.7 }}>
        一条影响规则 = <b style={{color:'var(--text)'}}>源 → 目标</b> + <b style={{color:'var(--text)'}}>when 生效条件</b>（表达式，如
        <code> surface_film == 'SiO₂' </code>，留空恒生效）+ 定量 <code>expr</code> 与定性
        <code> sign / mechanism </code>（均可留空）。Compute 时定量规则自动求值写入目标参数。
      </div>
      <datalist id="rule-params">{suggestions.map(s => <option key={s} value={s} />)}</datalist>
      {rules.map((r, i) => (
        <div key={r.id || i} style={{ border:'1px solid var(--border)', borderRadius:10, padding:'8px 10px', marginBottom:8, background:'var(--surface)' }}>
          <div className="row">
            <input list="rule-params" value={r.from || ''} placeholder="源(参数/属性)" onChange={e => upd(i, { from: e.target.value })} />
            <span style={{ color:'var(--accent)' }}>→</span>
            <input list="rule-params" value={r.to || ''} placeholder="目标参数" onChange={e => upd(i, { to: e.target.value })} />
            <select value={r.sign || ''} onChange={e => upd(i, { sign: e.target.value })} style={{ width:86 }}>
              <option value="">定性?</option><option value="+">正影响</option>
              <option value="-">负影响</option><option value="~">非单调</option>
            </select>
            <select value={r.reliability_score ?? 3} onChange={e => upd(i, { reliability_score: parseInt(e.target.value) })} style={{ width:70 }}>
              {[1,2,3,4,5].map(n => <option key={n} value={n}>{n}/5</option>)}
            </select>
            <span style={{ cursor:'pointer', color:'var(--muted)' }} title="删除规则"
              onClick={() => setRules(rs => rs.filter((_, j) => j !== i))}>✕</span>
          </div>
          <div className="row">
            {input(r.when || '', v => upd(i, { when: v }), "when 条件,如 surface_film == 'SiO₂'")}
            {input(r.expr || '', v => upd(i, { expr: v }), '定量表达式')}
          </div>
          <div className="row">
            {input(r.mechanism || '', v => upd(i, { mechanism: v }), '定性机理(为什么会影响)')}
            {input(r.source || '', v => upd(i, { source: v }), '来源', 130)}
          </div>
        </div>
      ))}
      <div className="row" style={{ marginTop:4 }}>
        <button className="btn ghost" onClick={() => setRules(rs => [...rs, {
          id: `ir-${Date.now().toString(36)}`, from: '', to: '', when: '', expr: '',
          sign: '', mechanism: '', scope: 'global', source: '', reliability_score: 3, enabled: true,
        }])}>+ 新规则</button>
        <button className="btn" onClick={async () => { await api.rulesSave(rules); refresh() }}>保存规则（{rules.length}）</button>
      </div>
    </div>
  )
}

/* ===== 设备编辑器:参数模板 + 承接/影响接口 + 公式模板 ===== */
function DeviceEditor({ eq, lib, refresh, onClose }: {
  eq: Equipment; lib: Library; refresh: () => void; onClose: () => void }) {
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
        <label style={{ width:60 }}>名称</label>
        <input value={name} onChange={e => setName(e.target.value)} />
      </div>

      <div className="iface-sec">参数模板（新拖入并选此设备的节点将继承）</div>
      <div className="row" style={{ fontSize:10, color:'var(--muted)' }}>
        <span style={{ width:110 }}>参数键</span><span style={{ width:110 }}>显示名</span>
        <span style={{ width:48 }}>单位</span><span style={{ width:58 }}>默认</span>
        <span style={{ width:58 }}>min</span><span style={{ width:58 }}>max</span>
      </div>
      {rows.map(([k, d], i) => (
        <div className="row" key={i} style={{ fontSize:11 }}>
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
        <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }}
          onClick={() => setRows(rs => [...rs, ['', { label:'', unit:'', default:0, min:0, max:0 }]])}>+ 参数</button>
      </div>

      <div className="iface-sec">承接 inputs（←）/ 影响 outputs（→）</div>
      {(['in', 'out'] as const).map(kind => {
        const list = kind === 'in' ? inputs : outputs
        const set = kind === 'in' ? setInputs : setOutputs
        return (
          <div className="row" key={kind} style={{ flexWrap:'wrap' }}>
            <span style={{ color:'var(--muted)', fontSize:11, width:26 }}>{kind === 'in' ? '←' : '→'}</span>
            {list.map(x => (
              <span key={x} className="chip" style={{ fontSize:11 }}>{x}
                <span className="chip-x" onClick={() => set(list.filter(y => y !== x))}>✕</span></span>
            ))}
            <select value="" style={{ width:130 }} onChange={e => { if (e.target.value) set([...list, e.target.value]) }}>
              <option value="">+ 添加…</option>
              {paramNames.filter(p => !list.includes(p)).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        )
      })}

      <div className="iface-sec">公式模板（输出 = 表达式）</div>
      {formulas.map(([out, expr], i) => (
        <div className="row" key={i}>
          <select style={{ width:110 }} value={out}
            onChange={e => setFormulas(fs => fs.map((f, j) => j === i ? [e.target.value, f[1]] : f))}>
            <option value="">— 选择输出 —</option>
            {[...new Set([...outputs, ...formulas.map(f => f[0])])].filter(Boolean)
              .map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input value={expr} placeholder="如 胶CD - 2 * bias_nm"
            onChange={e => setFormulas(fs => fs.map((f, j) => j === i ? [f[0], e.target.value] : f))} />
          <span className="chip-x" onClick={() => setFormulas(fs => fs.filter((_, j) => j !== i))}>✕</span>
        </div>
      ))}
      <div className="row">
        <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }}
          onClick={() => setFormulas(fs => [...fs, ['', '']])}>+ 公式</button>
      </div>

      <div className="row" style={{ marginTop:8 }}>
        <button className="btn" onClick={save} disabled={busy}>{busy ? '…' : '保存设备模板'}</button>
        <button className="btn ghost" onClick={onClose}>收起</button>
      </div>
    </div>
  )
}

/* ===== 参数:作用域(全局/工艺/机台) + 语义类别(可自定义) ===== */
function ParamsTab({ lib, refresh }: { lib: Library; refresh: () => void }) {
  const params: Record<string, any> = lib.params || {}
  const cats: string[] = (lib as any).param_categories || ['尺寸', '膜厚', '材料', '质量']
  const machines: any[] = (lib as any).machines || []
  const templates: { id: string; name: string }[] = Object.values(lib.equipment || {})
    .flat().map((e: any) => ({ id: e.id, name: e.name }))

  const [form, setForm] = useState({ name: '', unit: '', category: cats[0] || '尺寸', scope: 'global' })
  const [newCat, setNewCat] = useState('')

  const scopeLabel = (sc: string) => {
    if (!sc || sc === 'global') return '全局'
    const [kind, id] = sc.split(':')
    if (kind === 'process') return `工艺: ${templates.find(t => t.id === id)?.name || id}`
    if (kind === 'machine') return `机台: ${machines.find((m: any) => m.id === id)?.name || id}`
    return sc
  }
  const groups: Record<string, [string, any][]> = {}
  Object.entries(params).forEach(([k, v]: any) => {
    const sc = v.scope || 'global'
    ;(groups[sc] = groups[sc] || []).push([k, v])
  })

  return (
    <div>
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:10, lineHeight:1.7 }}>
        参数有两维分类：<b style={{color:'var(--text)'}}>作用域</b>（全局 / 某工艺模板 / 某机台）与
        <b style={{color:'var(--text)'}}>语义类别</b>（尺寸、膜厚、材料、质量，可自行增删）。
      </div>

      <div className="row">
        <input placeholder="参数名" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        <input placeholder="单位" style={{ width:64 }} value={form.unit} onChange={e => setForm({ ...form, unit: e.target.value })} />
        <select style={{ width:92 }} value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>
          {cats.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="row">
        <select value={form.scope} onChange={e => setForm({ ...form, scope: e.target.value })}>
          <option value="global">作用域：全局</option>
          <optgroup label="绑某工艺模板">
            {templates.map(t => <option key={t.id} value={`process:${t.id}`}>工艺: {t.name}</option>)}
          </optgroup>
          <optgroup label="绑某机台">
            {machines.map((m: any) => <option key={m.id} value={`machine:${m.id}`}>机台: {m.name}</option>)}
          </optgroup>
        </select>
        <button className="btn" onClick={async () => {
          if (!form.name.trim()) return
          await api.paramAdd(form.name.trim(), form.unit, form.category, form.scope)
          setForm({ name: '', unit: '', category: form.category, scope: form.scope })
          refresh()
        }}>添加参数</button>
      </div>

      <div className="iface-sec">语义类别（点 ✕ 删除；已用于参数的类别删前请确认）</div>
      <div className="row" style={{ flexWrap:'wrap' }}>
        {cats.map(c => (
          <span key={c} className="chip" style={{ fontSize:11 }}>{c}
            <span className="chip-x" onClick={async () => { await api.categoryRemove(c); refresh() }}>✕</span></span>
        ))}
        <input placeholder="新类别" style={{ width:110 }} value={newCat} onChange={e => setNewCat(e.target.value)} />
        <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }}
          onClick={async () => { if (newCat.trim()) { await api.categoryAdd(newCat.trim()); setNewCat(''); refresh() } }}>+ 类别</button>
      </div>

      {Object.entries(groups).map(([sc, list]) => (
        <div key={sc}>
          <div className="iface-sec">{scopeLabel(sc)}（{list.length}）</div>
          <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
            {list.map(([k, v]) => (
              <span key={k} className="chip" style={{ fontSize:11.5 }}
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
      <div style={{ color:'var(--muted)', fontSize:12, marginBottom:10, lineHeight:1.7 }}>
        机台 = <b style={{color:'var(--text)'}}>真实设备实例</b>（型号 + 编号 + 别名），挂在某个工艺模板下。
        同型号多台必须分开登记——否则机器差异会被当成工艺规律。
      </div>

      <div style={{ border:'1px solid var(--border)', borderRadius:10, padding:10, background:'var(--surface)', marginBottom:12 }}>
        <div className="row">
          <input placeholder="别名/编号,如 RIE200NL #1" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input placeholder="型号,如 RIE200NL" value={form.model} onChange={e => setForm({ ...form, model: e.target.value })} />
        </div>
        <div className="row">
          <input placeholder="厂家,如 SAMCO(日本)" value={form.vendor} onChange={e => setForm({ ...form, vendor: e.target.value })} />
          <input placeholder="最大样品,如 8 寸" style={{ width:110 }} value={form.max_sample} onChange={e => setForm({ ...form, max_sample: e.target.value })} />
        </div>
        <div className="row">
          <input placeholder="资产编号" style={{ width:130 }} value={form.serial} onChange={e => setForm({ ...form, serial: e.target.value })} />
          <select value={form.equipment_id} onChange={e => setForm({ ...form, equipment_id: e.target.value })}>
            <option value="">— 所属工艺模板 —</option>
            {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
        <div className="row">
          <input placeholder="位置" style={{ width:110 }} value={form.location} onChange={e => setForm({ ...form, location: e.target.value })} />
          <select style={{ width:110 }} value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>
            <option value="active">在用</option><option value="maintenance">维护中</option>
            <option value="down">停机</option><option value="retired">退役</option>
            <option value="待确认">待确认</option>
          </select>
          <input placeholder="备注" value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} />
        </div>
        <div className="row">
          <button className="btn" onClick={save}>{editId ? '保存修改' : '添加机台'}</button>
          {editId && <button className="btn ghost" onClick={() => { setEditId(null); setForm(blank) }}>取消</button>}
        </div>
      </div>

      {machines.map((m: any) => (
        <div key={m.id} className="row" style={{ padding:'7px 10px', border:'1px solid var(--border)', borderRadius:8, marginBottom:6 }}>
          <span style={{ flex:1 }}>
            <b style={{ fontSize:12.5 }}>{m.name}</b>
            <span style={{ color:'var(--muted)', fontSize:11 }}>
              {m.model ? ` · ${m.model}` : ''}{m.vendor ? ` · ${m.vendor}` : ''}{m.serial ? ` · #${m.serial}` : ''}{m.max_sample ? ` · ≤${m.max_sample}` : ''}
              {m.equipment_id ? ` · 模板 ${templates.find(t => t.id === m.equipment_id)?.name || '?'}` : ' · 未绑模板'}
              {m.location ? ` · ${m.location}` : ''}{m.notes ? ` · ${m.notes}` : ''}
            </span>
          </span>
          <button className="btn ghost" style={{ fontSize:11, padding:'3px 10px' }}
            onClick={() => { setEditId(m.id); setForm({ name: m.name, model: m.model || '', vendor: m.vendor || '', serial: m.serial || '', equipment_id: m.equipment_id || '', location: m.location || '', status: m.status || 'active', max_sample: m.max_sample || '', notes: m.notes || '' }) }}>编辑</button>
          <span className="chip-x" onClick={async () => { await api.machineRemove(m.id); refresh() }}>✕</span>
        </div>
      ))}
      {machines.length === 0 && <div style={{ color:'var(--muted)', fontSize:12 }}>（还没有机台）</div>}
    </div>
  )
}
