/* OpenNano 界面语言：**全英文**（2026-09-13 owner定：「放弃中文界面，只要全英文界面」）
 *
 * 为什么保留 i18n 这层、而不把英文写死在 JSX 里：
 *   ① **术语保真**是硬规矩 —— core 字段名 / 工序名 / 设备名 / 参数键 / 单位 / 状态枚举值**一律不翻**
 *      （翻了对不上库），所以必须有个地方能把"界面文案"和"数据术语"分开；
 *   ② 文案集中在一处才审得动（151+ 条），也才拦得住日后"随手写个中文标签"；
 *   ③ 真要恢复中文界面：加一份 `zh.ts` + 一个语言开关即可（`git show b34fe82:web/src/i18n/zh.ts` 就是全文）。
 *
 * 用法：
 *   const { t } = useI18n()
 *   t('topbar.run')                     // 取词
 *   t('sb.counts', { n: 13, e: 9 })     // 插值：目录里写 {n} / {e}
 */
import { createContext, useCallback, useContext, useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { en } from './en'

export type Lang = 'en'
export const LANG: Lang = 'en'

const DICT: Record<string, string> = en

/** 取词：找不到 key 时**原样回显 key**（宁可看见 `topbar.run` 也别看见空白） */
function translate(key: string, vars?: Record<string, unknown>): string {
  const s = DICT[key] ?? key
  if (!vars) return s
  return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] === undefined ? `{${k}}` : String(vars[k])))
}

interface Ctx { t: (key: string, vars?: Record<string, unknown>) => string }
const I18nCtx = createContext<Ctx>({ t: (k) => translate(k) })

export function I18nProvider({ children }: { children: ReactNode }) {
  useEffect(() => { document.documentElement.setAttribute('lang', 'en') }, [])
  const t = useCallback((key: string, vars?: Record<string, unknown>) => translate(key, vars), [])
  const value = useMemo(() => ({ t }), [t])
  return <I18nCtx.Provider value={value}>{children}</I18nCtx.Provider>
}

export function useI18n(): Ctx { return useContext(I18nCtx) }

/** 给**非函数组件**（class 组件、模块级工具）用的直接取词：语言只有英文一份，无需订阅。 */
export function t(key: string, vars?: Record<string, unknown>): string { return translate(key, vars) }
