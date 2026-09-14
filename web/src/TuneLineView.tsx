import { useI18n } from './i18n'
import { useMemo, useState } from 'react'

/** 参数调试线（O1 单点优化视图）。
 *  数据来自数据线建好的 `v_tune_line` 视图（缺 = NULL、不补值）；
 *  本组件只**呈现**：参数轴 + 响应列 + 可比性提示 + 一张小散点图。
 *  不许做的事：补值、插值、把不同量纲画到一根轴上。 */

const PARAM_LABEL: Record<string, string> = {
  /* 参数键（core 键）→ 界面短标签。**键不翻**，只翻显示用的标签 */
  t_set_s: 't set (s)', t_dwell_s: 't dwell (s)', source_w: 'source W', bias_w: 'bias W',
  cf4_sccm: 'CF₄', sf6_sccm: 'SF₆',
}
const RESP_LABEL: Record<string, string> = {
  /* 量名词（core 键）→ 界面短标签。**键不翻**，只翻这些显示用标签 */
  t_set_s: 't set (s)', t_dwell_s: 't dwell (s)', source_w: 'source W', bias_w: 'bias W',
  cd_delta_nm: 'cd delta', depth_nm: 'depth', er_nm_min: 'etch rate',
  selectivity: 'selectivity', film_thickness_nm: 'thickness', stress_mpa: 'stress',
  refractive_index: 'n',
}

function num(v: any): number | null {
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : null
}

export default function TuneLineView({ series }: { series: any }) {
  const { t } = useI18n()
  const params: string[] = series.param_cols || []
  const resps: string[] = series.response_cols || []
  const comparable: Record<string, number> = series.comparable_responses || {}

  const [xCol, setXCol] = useState(() =>
    params.includes('bias_w') ? 'bias_w' : params[0] || '')
  const [yCol, setYCol] = useState(() =>
    Object.keys(comparable)[0] || resps[0] || '')

  const points = useMemo(() => {
    return (series.steps || [])
      .map((s: any) => ({
        step: s.tune_step, run: s.run_id,
        x: num(s[xCol]), y: num(s[yCol]),
      }))
      .filter(p => p.x != null && p.y != null) as { step: string; run: string; x: number; y: number }[]
  }, [series, xCol, yCol])

  // 图尺寸与坐标映射
  const W = 460, H = 168, PAD = { l: 46, r: 12, t: 12, b: 26 }
  const xs = points.map(p => p.x), ys = points.map(p => p.y)
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
  const sx = (x: number) => PAD.l + (x1 > x0 ? (x - x0) / (x1 - x0) : 0.5) * (W - PAD.l - PAD.r)
  const sy = (y: number) => PAD.t + (1 - (y1 > y0 ? (y - y0) / (y1 - y0) : 0.5)) * (H - PAD.t - PAD.b)

  return (
    <div className="tune-line">
      <div className="tl-head">
        <b>{series.tune_id}</b>
        <span className="tl-meta">{series.stage} · {series.sample_id} · {t('tl.rounds', { n: series.n_steps })}</span>
        <span className={'tl-note' + (Object.keys(comparable).length ? '' : ' warn')}>
          {series.comparability_note}
        </span>
      </div>

      <div className="tl-cols">
        <div className="tl-table-wrap">
          <table className="tbl tl-tbl">
            <thead>
              <tr>
                <th>{t('tl.round')}</th><th>run</th><th>{t('tl.date')}</th>
                {params.map(c => <th key={c}>{PARAM_LABEL[c] || c}</th>)}
                {resps.map(c => <th key={c} className="resp">{RESP_LABEL[c] || c}</th>)}
              </tr>
            </thead>
            <tbody>
              {(series.steps || []).map((s: any) => (
                <tr key={s.run_id}>
                  <td><b>{s.tune_step}</b></td>
                  <td><code>{(s.run_id || '').split('-').slice(-2).join('-')}</code></td>
                  <td className="dim">{s.date}</td>
                  {params.map(c => (
                    <td key={c}>{s[c] ?? <span className="na">—</span>}</td>
                  ))}
                  {resps.map(c => (
                    <td key={c} className="resp">{s[c] ?? <span className="na">—</span>}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="tl-chart">
          <div className="tl-chart-ctl">
            <label>{t('tl.xAxis')}
              <select value={xCol} onChange={e => setXCol(e.target.value)}>
                {params.map(c => <option key={c} value={c}>{PARAM_LABEL[c] || c}</option>)}
              </select>
            </label>
            <label>{t('tl.yAxis')}
              <select value={yCol} onChange={e => setYCol(e.target.value)}>
                {resps.map(c => (
                  <option key={c} value={c}>
                    {RESP_LABEL[c] || c}{comparable[c] ? '' : t('tl.few')}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {points.length >= 2 ? (
            <svg width={W} height={H} className="tl-svg">
              <line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} className="axis" />
              <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b} className="axis" />
              <text x={W - PAD.r} y={H - 6} textAnchor="end" className="axis-label">
                {PARAM_LABEL[xCol] || xCol}
              </text>
              <text x={6} y={PAD.t + 4} className="axis-label">{RESP_LABEL[yCol] || yCol}</text>
              {[x0, x1].map((v, i) => (
                <text key={i} x={sx(v)} y={H - PAD.b + 14} textAnchor="middle" className="tick">{v}</text>
              ))}
              {[y0, y1].map((v, i) => (
                <text key={i} x={PAD.l - 6} y={sy(v) + 3} textAnchor="end" className="tick">{v}</text>
              ))}
              <polyline
                points={points.map(p => `${sx(p.x)},${sy(p.y)}`).join(' ')}
                className="tl-line" />
              {points.map(p => (
                <g key={p.run}>
                  <circle cx={sx(p.x)} cy={sy(p.y)} r={4} className="tl-dot" />
                  <text x={sx(p.x) + 7} y={sy(p.y) - 5} className="tl-step">{p.step}</text>
                </g>
              ))}
            </svg>
          ) : (
            <div className="tl-empty">
              {yCol && !comparable[yCol]
                ? t('tl.notEnough', { y: RESP_LABEL[yCol] || yCol })
                : t('tl.nothing')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
