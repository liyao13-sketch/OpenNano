import { useI18n } from './i18n'
import { useEffect, useState } from 'react'
import { api } from './api'

export default function KbBrowser({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const [entries, setEntries] = useState<any[]>([])
  const [stats, setStats] = useState<any>({})
  const [q, setQ] = useState('')
  const [minRel, setMinRel] = useState(0)
  /* 知识层（契约 §三 三层 taxonomy）：设备级 / 机理·理论 / 文献参数 / **手册·出厂值**。
     分族检索的意义：`THEORY_*` 是**普适**的（按设备过滤时默认带上），而 `MANUAL_*`
     是**某台设备/某份 TDS** 的出厂值，绝不能跟着普适机理一起铺给所有设备。 */
  const [layer, setLayer] = useState('')

  const load = (query?: string, lay?: string) => {
    api.kb(query, lay ?? layer).then(setEntries)
    api.kbStats().then(setStats)
  }
  useEffect(() => { load() }, [])

  const shown = entries.filter(e => e.reliability_score >= minRel)
  /** 层标：由 process_type 前缀判（与后端同一套前缀，不另立真相） */
  const layerOf = (pt: string) => pt.startsWith('MANUAL_') ? 'manual'
    : pt.startsWith('THEORY_') ? 'theory' : pt.startsWith('LIT_') ? 'lit' : 'device'
  const relColor = (r: number) => r >= 4 ? 'var(--ok)' : r === 3 ? 'var(--info)' : r === 2 ? 'var(--warn)' : 'var(--bad)'

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(8,9,10,.72)', backdropFilter:'blur(2px)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:2000 }}>
      <div style={{ width:860, maxHeight:'84%', background:'var(--panel)', border:'1px solid var(--border)', borderRadius:14, display:'flex', flexDirection:'column', overflow:'hidden' }}>
        <div style={{ padding:'12px 16px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between', alignItems:'center', gap:12 }}>
          <b>{t('kb.title', { n: stats.total || 0 })}</b>
          <div style={{ display:'flex', gap:8, alignItems:'center' }}>
            <input placeholder={t('kb.search')} value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => e.key==='Enter' && load(q)}
              style={{ padding:'6px 10px', background:'var(--surface)', color:'var(--text)', border:'1px solid var(--border)', borderRadius:6, width:220 }} />
            <select value={layer} onChange={e => { setLayer(e.target.value); load(q, e.target.value) }}
              title={t('kb.layerTip')}
              style={{ padding:'6px 8px', background:'var(--surface)', color:'var(--text)', border:'1px solid var(--border)', borderRadius:6 }}>
              <option value="">{t('kb.layerAll')}</option>
              <option value="device">{t('kb.layerDevice')}</option>
              <option value="theory">{t('kb.layerTheory')}</option>
              <option value="lit">{t('kb.layerLit')}</option>
              <option value="manual">{t('kb.layerManual')}</option>
            </select>
            <select value={minRel} onChange={e => setMinRel(parseInt(e.target.value))}
              style={{ padding:'6px 8px', background:'var(--surface)', color:'var(--text)', border:'1px solid var(--border)', borderRadius:6 }}>
              <option value={0}>{t('kb.allRel')}</option>
              <option value={4}>{t('kb.rel4')}</option>
              <option value={2}>{t('kb.rel2')}</option>
            </select>
            <span style={{ cursor:'pointer', color:'var(--muted)' }} onClick={onClose}>✕</span>
          </div>
        </div>
        <div style={{ flex:1, overflowY:'auto', padding:12 }}>
          {shown.map(e => (
            <div key={e.id} style={{ border:'1px solid var(--border)', borderRadius:10, padding:'10px 12px', marginBottom:8, background:'var(--surface)' }}>
              <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
                <span style={{ background: relColor(e.reliability_score), color:'var(--bg)', borderRadius:6, padding:'1px 8px', fontSize: 'var(--fs-xs)', fontWeight:'var(--fw-bold)' }}>{e.reliability_score}/5</span>
                <b style={{ fontSize: 'var(--fs-md)' }}>{e.title}</b>
                <span style={{ color: layerOf(e.process_type)==='manual' ? 'var(--accent-text)' : 'var(--muted)',
                  fontSize: 'var(--fs-xs)', fontWeight: layerOf(e.process_type)==='manual' ? 'var(--fw-semibold)' : 'var(--fw-normal)' }}>
                  {t({ manual:'kb.layerManualTag', theory:'kb.layerTheoryTag', lit:'kb.layerLitTag', device:'kb.layerDeviceTag' }[layerOf(e.process_type)])} · {e.process_type}</span>
              </div>
              <div style={{ fontSize: 'var(--fs-xs)', color:'var(--muted)' }}>
                {t('kb.material')} {e.material?.material || '—'} · {t('kb.source')} {e.source}
                {Object.keys(e.results || {}).length > 0 && <> · {t('kb.results')} {Object.entries(e.results).map(([k,v]) => `${k}=${v}`).join(', ')}</>}
              </div>
            </div>
          ))}
          {shown.length === 0 && <div style={{ color:'var(--muted)' }}>{t('kb.none')}</div>}
        </div>
      </div>
    </div>
  )
}
