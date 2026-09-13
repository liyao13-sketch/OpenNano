/* OpenNano i18n 机制（2026-09-13 建 · owner：「做」）
 *
 * 设计取舍（详见 `docs/i18n.md`）：
 *   · **不做两套界面**，只做"语言切换 + 术语保真"；
 *   · 中文是主语言（默认），英文为第二语言；
 *   · 只翻**界面文案**；core 字段名 / 工序名 / 设备名 / 单位 / 状态枚举值**保持英文原样**
 *     —— 它们是契约里的键，翻成中文就再也对不上库；
 *   · 不引三方 i18n 库：两种语言、无复数/性数规则，一个对象 + 一个 hook 足够。
 *
 * 用法：
 *   const { t, lang, setLang } = useI18n()
 *   t('topbar.run')                    // 取词
 *   t('sb.counts', { n: 13, e: 9 })    // 插值：目录里写 {n} / {e}
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { zh } from './zh'
import { en } from './en'

export type Lang = 'zh' | 'en'
export const LANGS: { key: Lang; label: string; short: string }[] = [
  { key: 'zh', label: '中文', short: '中' },
  { key: 'en', label: 'English', short: 'EN' },
]

const DICT: Record<Lang, Record<string, string>> = { zh, en }
const KEY = 'opennano.lang'

/** 取词：找不到 key 时**原样回显 key**（宁可看见 `topbar.run` 也别看见空白） */
function translate(lang: Lang, key: string, vars?: Record<string, unknown>): string {
  const s = DICT[lang]?.[key] ?? DICT.zh[key] ?? key
  if (!vars) return s
  return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] === undefined ? `{${k}}` : String(vars[k])))
}

interface Ctx {
  lang: Lang
  setLang: (l: Lang) => void
  t: (key: string, vars?: Record<string, unknown>) => string
}

const I18nCtx = createContext<Ctx>({
  lang: 'zh', setLang: () => {}, t: (k) => translate('zh', k),
})

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const saved = (typeof localStorage !== 'undefined' && localStorage.getItem(KEY)) || ''
    if (saved === 'zh' || saved === 'en') return saved
    /* 没存过就跟随浏览器语言：非中文环境给英文（对外演示/投稿的第一印象） */
    const nav = typeof navigator !== 'undefined' ? navigator.language || '' : ''
    return /^zh/i.test(nav) ? 'zh' : 'en'
  })
  const setLang = useCallback((l: Lang) => {
    setLangState(l)
    try { localStorage.setItem(KEY, l) } catch { /* 隐私模式下写不了就算了 */ }
  }, [])
  useEffect(() => {
    /* `<html lang>` 要跟着走：影响断行规则、字体回退与朗读器 */
    document.documentElement.setAttribute('lang', lang === 'zh' ? 'zh-CN' : 'en')
  }, [lang])
  const t = useCallback((key: string, vars?: Record<string, unknown>) =>
    translate(lang, key, vars), [lang])
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t])
  return <I18nCtx.Provider value={value}>{children}</I18nCtx.Provider>
}

export function useI18n(): Ctx { return useContext(I18nCtx) }
