import { useCallback, useEffect, useRef, useState } from 'react'

export type DockTab = {
  key: string
  label: string
  render: () => React.ReactNode
}

/** 底部停靠面板：对话 / 批次 / 日志 / 问题 同住一处。
 *  顶边可拖拽调高（记忆在 localStorage）；双击页签栏或点 chevron 收起。
 *  设计依据（owner 2026-09-12）：LLM 对话要"在画布下方"，且不再左右两根滚动条抢着用。 */
export default function Dock({ tabs, active, onTab, defaultHeight = 240 }: {
  tabs: DockTab[]
  active: string
  onTab: (key: string) => void
  defaultHeight?: number
}) {
  const LS_KEY = 'opennano.dock.height'
  const [height, setHeight] = useState<number>(() => {
    const v = Number(localStorage.getItem(LS_KEY))
    return v >= 140 && v <= 800 ? v : defaultHeight
  })
  const [collapsed, setCollapsed] = useState(false)
  const drag = useRef<{ y: number; h: number } | null>(null)

  const onMove = useCallback((e: MouseEvent) => {
    if (!drag.current) return
    const h = Math.min(800, Math.max(140, drag.current.h + (drag.current.y - e.clientY)))
    setHeight(h)
  }, [])
  const onUp = useCallback(() => {
    if (drag.current) {
      localStorage.setItem(LS_KEY, String(height))
      drag.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [height])

  useEffect(() => {
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [onMove, onUp])

  const cur = tabs.find(t => t.key === active) || tabs[0]

  return (
    <div className="dock" style={{ height: collapsed ? 34 : height }}>
      <div className="dock-handle" title="拖拽调整高度 · 双击收起/展开"
        onMouseDown={e => {
          drag.current = { y: e.clientY, h: height }
          document.body.style.cursor = 'row-resize'
          document.body.style.userSelect = 'none'
        }}
        onDoubleClick={() => setCollapsed(c => !c)} />
      <div className="dock-tabs">
        {tabs.map(t => (
          <div key={t.key} className={'ptab' + (cur.key === t.key && !collapsed ? ' active' : '')}
            onClick={() => { onTab(t.key); setCollapsed(false) }}>
            {t.label}
          </div>
        ))}
        <span className="spacer" />
        <span className="dock-chev" title={collapsed ? '展开' : '收起'}
          onClick={() => setCollapsed(c => !c)}>
          {collapsed ? '▴' : '▾'}
        </span>
      </div>
      {!collapsed && <div className="dock-body">{cur.render()}</div>}
    </div>
  )
}
