import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { useLanguage } from '../i18n'
import { queryClient } from '../lib/queryClient'
import {
  ATHLETE_HYPOTHESES_QUERY_KEY,
  ATHLETE_OPEN_QUESTIONS_QUERY_KEY,
  ATHLETE_PREDICTIONS_QUERY_KEY,
  ATHLETE_TRAITS_QUERY_KEY,
  COACH_TIMELINE_QUERY_KEY,
  VALIDATION_EXPERIMENTS_QUERY_KEY,
} from '../queries/keys'
import { fetchPlanTranslations, writePlanTranslations } from '../services/ai'
import { useAppStore, type TrainingDay } from '../store/useAppStore'
import { usePlan } from '../queries/plan'

/** The interface languages the server translates the plan into (ai-trainer-ops#121). */
const TRANSLATED = new Set(['de'])

/** How many writes one state of the plan gets. One write takes up to 60 texts,
 *  so this covers a long plan; a model that keeps leaving texts out stops it
 *  earlier, because a write that adds nothing ends the rounds. */
export const MAX_WRITE_ROUNDS = 5

/** The texts of one session the athlete reads, the ones the server translates. */
export function sessionTexts(day: TrainingDay): (string | undefined)[] {
  return [
    day.title,
    day.description,
    day.workoutPurpose,
    ...(day.keyFocusPoints ?? []),
    ...(day.strengthExercises ?? []).map((exercise) => exercise.exercise),
  ]
}

/** Every text of the plan the athlete reads, as one string. It changes when a
 *  writer changes a text, which is when translations may be owed. */
function planTextSignature(plan: TrainingDay[]): string {
  return plan.flatMap(sessionTexts).filter(Boolean).join('\n')
}

/** The queries that bring the coach's own texts: hypotheses, questions,
 *  experiments, traits and predictions. The server translates them with the
 *  plan (ai-trainer-ops#215). */
const COACH_TEXT_QUERIES: ReadonlySet<unknown> = new Set([
  COACH_TIMELINE_QUERY_KEY,
  ATHLETE_TRAITS_QUERY_KEY,
  ATHLETE_HYPOTHESES_QUERY_KEY,
  ATHLETE_OPEN_QUESTIONS_QUERY_KEY,
  VALIDATION_EXPERIMENTS_QUERY_KEY,
  ATHLETE_PREDICTIONS_QUERY_KEY,
])

const subscribeToCoachTexts = (notify: () => void) =>
  queryClient.getQueryCache().subscribe((event) => {
    if (event.type === 'updated' && COACH_TEXT_QUERIES.has(event.query.queryKey[0])) notify()
  })

const coachTextsReceivedAt = () =>
  Math.max(
    0,
    ...queryClient
      .getQueryCache()
      .findAll()
      .filter((query) => COACH_TEXT_QUERIES.has(query.queryKey[0]))
      .map((query) => query.state.dataUpdatedAt),
  )

/** When coach texts last arrived: a text the coach wrote while the app is open
 *  is asked for then, not only when a plan text changes (ai-trainer-ops#215). */
function useCoachTextsReceivedAt(): number {
  return useSyncExternalStore(subscribeToCoachTexts, coachTextsReceivedAt)
}

/**
 * Keeps the store's plan translations current for a German interface
 * (ai-trainer-ops#121). Mounted once, in the signed-in layout.
 *
 * Reads the stored ones whenever a plan text changes or coach texts arrive.
 * While texts are missing it asks the server to write them, in rounds, once per
 * set of missing texts, which the server names with a key (ai-trainer-ops#215):
 * a text the model keeps leaving out, or a failed write, is not asked for again
 * until another text is missing too. A run that is overtaken still keeps what
 * it was given, since the translations are keyed by the English text and so
 * are never wrong.
 * While a run writes, the store says the missing texts are on their way
 * (ai-trainer-ops#271).
 */
export function usePlanTranslationSync(): void {
  const language = useLanguage()
  const authToken = useAppStore((s) => s.authToken)
  const plan = usePlan()
  const mergePlanTranslations = useAppStore((s) => s.mergePlanTranslations)
  const setPending = useAppStore((s) => s.setPlanTranslationsPending)
  const signature = useMemo(() => planTextSignature(plan), [plan])
  const coachTextsAt = useCoachTextsReceivedAt()
  const asked = useRef(new Set<string>())
  const currentLanguage = useRef(language)
  // Runs that are writing. An overtaken run may still be, so the store is told
  // the writes are over only when the last one is.
  const writing = useRef(0)

  useEffect(() => {
    currentLanguage.current = language
  }, [language])

  useEffect(() => {
    if (!authToken || (!signature && !coachTextsAt) || !TRANSLATED.has(language)) return
    let cancelled = false
    let key: string | null = null
    const keep = (translations: Record<string, string>) => {
      if (currentLanguage.current === language) mergePlanTranslations(translations)
    }
    const load = async () => {
      let writes = false
      try {
        let answer = await fetchPlanTranslations(authToken, language)
        if (answer.language !== language) return
        keep(answer.translations)
        key = `${language}\n${answer.missingKey ?? ''}`
        if (asked.current.has(key)) return
        if (answer.missing && !cancelled) {
          writes = true
          writing.current += 1
          setPending(true)
        }
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
        // A run torn down mid-request leaves the write to the one replacing it.
        if (!cancelled && key) asked.current.add(key)
        // The English texts stay; the plan reads as before.
        console.warn('[PlanTranslations] could not load the plan in', language, error)
      } finally {
        if (writes) {
          writing.current -= 1
          setPending(writing.current > 0)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authToken, language, signature, coachTextsAt, mergePlanTranslations, setPending])
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

/**
 * Whether a plan text is still on its way into the interface language: the
 * server is writing the missing translations and this one has none yet
 * (ai-trainer-ops#271). Its English shows meanwhile, dimmed, under a note that
 * the German follows; once the writes are over, a text left without one shows
 * as before.
 */
export function usePlanTextPending(): (text: string | null | undefined) => boolean {
  const language = useLanguage()
  const pending = useAppStore((s) => s.planTranslationsPending)
  const translations = useAppStore((s) => s.planTranslations)
  return useCallback(
    (text: string | null | undefined): boolean => {
      const key = text?.trim()
      if (!key || !pending || !TRANSLATED.has(language)) return false
      return translations[key] === undefined
    },
    [language, pending, translations],
  )
}
