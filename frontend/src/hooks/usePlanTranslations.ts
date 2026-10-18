import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useLanguage } from '../i18n'
import { fetchPlanTranslations, writePlanTranslations } from '../services/ai'
import { useAppStore, type TrainingDay } from '../store/useAppStore'

/** The interface languages the server translates the plan into (ai-trainer-ops#121). */
const TRANSLATED = new Set(['de'])

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
 * Reads the stored ones whenever a plan text changes, and asks the server to
 * write the missing ones once per state of the plan: a failed write leaves the
 * English texts in place and is not retried until the plan changes.
 */
export function usePlanTranslationSync(): void {
  const language = useLanguage()
  const authToken = useAppStore((s) => s.authToken)
  const plan = useAppStore((s) => s.trainingPlan)
  const setPlanTranslations = useAppStore((s) => s.setPlanTranslations)
  const signature = useMemo(() => planTextSignature(plan), [plan])
  const asked = useRef(new Set<string>())

  useEffect(() => {
    if (!authToken || !signature || !TRANSLATED.has(language)) return
    let cancelled = false
    const load = async () => {
      try {
        const stored = await fetchPlanTranslations(authToken, language)
        if (cancelled || stored.language !== language) return
        setPlanTranslations(stored.translations)
        const key = `${language}\n${signature}`
        if (!stored.missing || asked.current.has(key)) return
        asked.current.add(key)
        const written = await writePlanTranslations(authToken, language)
        if (!cancelled && written.language === language) setPlanTranslations(written.translations)
      } catch (error) {
        // The English texts stay; the plan reads as before.
        console.warn('[PlanTranslations] could not load the plan in', language, error)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authToken, language, signature, setPlanTranslations])
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
