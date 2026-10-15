import { useCallback } from 'react'
import { useAppStore } from '../store/useAppStore'
import { DE } from './de'

/** The interface speaks German or English (ai-trainer-ops#103 Q27, the owner's
 * answer): the browser's language decides, English when it is neither, and
 * Settings can fix it either way. */
export type Language = 'en' | 'de'
export type LanguageChoice = 'auto' | Language

export const LANGUAGE_CHOICES: readonly LanguageChoice[] = ['auto', 'en', 'de']

/** German when the browser prefers it before English, otherwise English. */
export function browserLanguage(
  languages: readonly string[] = typeof navigator === 'undefined'
    ? []
    : navigator.languages?.length
      ? navigator.languages
      : [navigator.language],
): Language {
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
  const template = language === 'de' ? (DE[text] ?? text) : text
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  )
}

export type Translate = (text: string, vars?: Record<string, string | number>) => string

export function useLanguage(): Language {
  return resolveLanguage(useAppStore((s) => s.uiLanguage))
}

export function useT(): Translate {
  const language = useLanguage()
  return useCallback((text, vars) => translate(language, text, vars), [language])
}

/** The BCP 47 tag for dates and numbers in the interface language. */
export function localeTag(language: Language): string {
  return language === 'de' ? 'de-DE' : 'en-GB'
}
