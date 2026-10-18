import { useCallback, type ReactNode } from 'react'
import { format } from 'date-fns'
import { de as germanDates } from 'date-fns/locale'
import { useAppStore } from '../store/useAppStore'
import { DE, DE_PATTERNS } from './de'

/** The interface speaks German or English (ai-trainer-ops#103 Q27, the owner's
 * answer): the browser's language decides, English when it is neither, and
 * Settings can fix it either way. */
export type Language = 'en' | 'de'
export type LanguageChoice = 'auto' | Language

export const LANGUAGE_CHOICES: readonly LanguageChoice[] = ['auto', 'en', 'de']

function navigatorLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return []
  return navigator.languages?.length ? navigator.languages : [navigator.language]
}

/** German when the browser prefers it before English, otherwise English. */
export function browserLanguage(languages: readonly string[] = navigatorLanguages()): Language {
  for (const tag of languages) {
    const base = tag?.toLowerCase().split('-')[0]
    if (base === 'de' || base === 'en') return base
  }
  return 'en'
}

export function resolveLanguage(choice: LanguageChoice): Language {
  return choice === 'auto' ? browserLanguage() : choice
}

/** The English source text is the key, so a string nobody translated yet stays
 * English rather than turning into a key. `{name}` takes a value from `vars`. */
export function translate(
  language: Language,
  text: string,
  vars?: Record<string, string | number>,
): string {
  // Own keys only: `constructor` or `toString` must not resolve to Object's.
  if (language === 'de' && !Object.hasOwn(DE, text)) {
    // A server message with numbers in it has no key of its own (#121).
    for (const [pattern, german] of DE_PATTERNS) {
      const match = text.match(pattern)
      if (match) return german(match)
    }
  }
  const template = language === 'de' && Object.hasOwn(DE, text) ? DE[text] : text
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  )
}

export type Translate = (text: string, vars?: Record<string, string | number>) => string

/** The identity translation, for helpers called outside a component. */
export const english: Translate = (text, vars) => translate('en', text, vars)

/** A translated sentence with elements inside it, such as a link: each `{name}`
 * slot becomes `nodes[name]`, which needs a `key`. */
export function slots(text: string, nodes: Record<string, ReactNode>): ReactNode[] {
  return text.split(/\{(\w+)\}/).map((part, i) => (i % 2 ? nodes[part] : part))
}

export function useLanguage(): Language {
  return resolveLanguage(useAppStore((s) => s.uiLanguage))
}

export function useT(): Translate {
  const language = useLanguage()
  return useCallback((text, vars) => translate(language, text, vars), [language])
}

/** The BCP 47 tag for dates and numbers in the interface language. English
 * keeps the browser's own English (a US browser keeps US dates), and falls back
 * to en-GB for a browser that names none, such as a German one set to English. */
export function localeTag(
  language: Language,
  languages: readonly string[] = navigatorLanguages(),
): string {
  if (language === 'de') return 'de-DE'
  return languages.find((tag) => tag?.toLowerCase().split('-')[0] === 'en') ?? 'en-GB'
}

/** A number with a fixed count of decimals in the interface language: "42.5"
 * or "42,5". */
export function formatDecimal(value: number, digits: number, language: Language): string {
  return value.toLocaleString(localeTag(language), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** Today's long date in the interface language: "Donnerstag, 15. Oktober 2026"
 * or "Thursday, October 15, 2026". */
export function formatLongDate(date: Date, language: Language): string {
  return language === 'de'
    ? format(date, 'EEEE, d. MMMM yyyy', { locale: germanDates })
    : format(date, 'EEEE, MMMM d, yyyy')
}

