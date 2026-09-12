import { Component, type ReactNode } from 'react'

/** 局部错误边界：**一个节点/面板出错，不许把整个界面变白**。
 *
 * 起因（2026-09-13 owner实测）：点 DRIE 后"什么都不见了" —— 续做生成的模块缺 `key_values`，
 * 面板里 `m.key_values[k]` 抛 TypeError；React 在无边界时会卸载整棵树 ⇒ 白屏，且看不出原因。
 * 现在：出错只把**那一块**换成可读的提示（含错误信息与"重试"），其余界面照常可用。
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; label?: string; onReset?: () => void },
  { err: Error | null }
> {
  state = { err: null as Error | null }

  static getDerivedStateFromError(err: Error) {
    return { err }
  }

  componentDidCatch(err: Error, info: unknown) {
    // 保留现场：控制台能看到组件栈，便于定位是哪个模块的数据坏了
    console.error('[OpenNano] 渲染出错：', this.props.label || '', err, info)
  }

  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="errbox">
        <div className="errbox-h">⚠️ {this.props.label || '这块界面'}渲染出错（其余部分仍可用）</div>
        <div className="errbox-msg">{String(this.state.err?.message || this.state.err)}</div>
        <div className="errbox-tip">
          常见原因：模块数据缺字段（例如续做/导入的节点没有 <code>key_values</code>）。
          可在节点上右键「重置本节点及下游」，或用「从 core 回灌画布」重建。
        </div>
        <button className="btn ghost"
          onClick={() => { this.setState({ err: null }); this.props.onReset?.() }}>重试渲染</button>
      </div>
    )
  }
}
