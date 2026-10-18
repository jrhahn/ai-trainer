import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useLanguage } from '../i18n'
import { fetchPlanTranslations, writePlanTranslations } from '../services/ai'
import { useAppStore, type TrainingDay } from '../store/useAppStore'

/** The interface languages the server translates the plan into (ai-trainer-ops#121). */
const TRANSLATED = new Set(['de'])

/** How many writes one state of the plan gets. One write takes up to 60 texts,
 *  so this covers a long plan; a model that keeps leaving texts out stops it
 *  earlier, because a write that adds nothing ends the rounds. */
export const MAX_WRITE_ROUNDS = 5

/** Every text of the plan the athlete reads, as one string. It changes when a
 *  writer changes a text, which is when translations may be owed. */
function planTextSignature(plan: TrainingDay[]): string {
  return plan
    .flatMap((day) => [
      day.title,
      day.description,
      day.workoutPurpose,
      ...(day.keyFocusPoints ?? []),
      ...(day.strengthExercises ?? []).map((exercise) => exercise.exercise),
    ])
    .filter(Boolean)
    .join('\n')
}

/**
 * Keeps the store's plan translations current for a German interface
 * (ai-trainer-ops#121). Mounted once, in the signed-in layout.
 *
 * Reads the stored ones whenever a plan text changes. While texts are missing
 * it asks the server to write them, in rounds, once per state of the plan: a
 * failed write leaves the English texts in place and is not retried until the
 * plan changes. A run that is overtaken still keeps what it was given, since
 * the translations are keyed by the English text and so are never wrong.
 */
export function usePlanTranslationSync(): void {
  const language = useLanguage()
  const authToken = useAppStore((s) => s.authToken)
  const plan = useAppStore((s) => s.trainingPlan)
  const mergePlanTranslations = useAppStore((s) => s.mergePlanTranslations)
  const signature = useMemo(() => planTextSignature(plan), [plan])
  const asked = useRef(new Set<string>())
  const currentLanguage = useRef(language)

  useEffect(() => {
    currentLanguage.current = language
  }, [language])

  useEffect(() => {
    if (!authToken || !signature || !TRANSLATED.has(language)) return
    let cancelled = false
    const key = `${language}\n${signature}`
    const keep = (translations: Record<string, string>) => {
      if (currentLanguage.current === language) mergePlanTranslations(translations)
    }
    const load = async () => {
      try {
        let answer = await fetchPlanTranslations(authToken, language)
        if (answer.language !== language) return
        keep(answer.translations)
        if (asked.current.has(key)) return
        for (let round = 0; answer.missing && round < MAX_WRITE_ROUNDS && !cancelled; round++) {
          const held = Object.keys(answer.translations).length
          answer = await writePlanTranslations(authToken, language)
          if (answer.language !== language) return
          keep(answer.translations)
          if (Object.keys(answer.translations).length <= held) break
        }
        // A run cut short leaves the rest to the run that replaced it.
        if (!cancelled) asked.current.add(key)
      } catch (error) {
        asked.current.add(key)
        // The English texts stay; the plan reads as before.
        console.warn('[PlanTranslations] could not load the plan in', language, error)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authToken, language, signature, mergePlanTranslations])
}

/** A plan text as the athlete reads it: translated when the interface language has one. */
export function usePlanText(): <T extends string | null | undefined>(text: T) => T | string {
  const language = useLanguage()
  const translations = useAppStore((s) => s.planTranslations)
  return useCallback(
    <T extends string | null | undefined>(text: T): T | string =>
      text && TRANSLATED.has(language) ? (translations[text.trim()] ?? text) : text,
    [language, translations],
  )
}
