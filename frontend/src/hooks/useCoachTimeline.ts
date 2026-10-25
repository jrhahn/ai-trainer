import { useEffect, useMemo, useRef, useState } from 'react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import {
  answerAthleteInquiry,
  dismissAthleteInquiry,
  fetchCurrentUser,
  fetchPlanHistory,
  fetchAthleteInquiries,
  fetchAthleteOpenQuestions,
  fetchAthleteHypotheses,
  fetchValidationExperiments,
} from '../services/user'
import type {
  AthleteExperiment,
  AthleteHypothesis,
  AthleteInquiry,
  AthleteOpenQuestion,
  PlanDayHistoryEntry,
} from '../services/user'
import { planUpdateEvents, recommendationEvents } from '../utils/coachTimeline'
import type { TimelineEvent } from '../utils/coachTimeline'
import { localeTag, useLanguage, useT } from '../i18n'
import { usePlanText } from './usePlanTranslations'

/** The coach timeline's plan changes and recommendations, and the question
 *  pinned above it, fetched again after each exchange (#418, #506). An answer
 *  that also asks for something goes on through `forwardToCoach`.
 *  Out of `AIChat` (ai-trainer-ops#274). */
export function useCoachTimeline(forwardToCoach: (answer: string) => Promise<void>) {
  const t = useT()
  const planText = usePlanText()
  const { authToken, chatHistory, addChatMessage, setUserProfile } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      chatHistory: s.chatHistory,
      addChatMessage: s.addChatMessage,
      setUserProfile: s.setUserProfile,
    }))
  )
  // The raw lists, not their sentences: the sentences follow the interface
  // language, which can change while the chat is open (ai-trainer-ops#103 Q27).
  const [timelineSource, setTimelineSource] = useState<{
    history: PlanDayHistoryEntry[]
    questions: AthleteOpenQuestion[]
    hypotheses: AthleteHypothesis[]
    experiments: AthleteExperiment[]
  }>({ history: [], questions: [], hypotheses: [], experiments: [] })
  const language = useLanguage()
  const timelineEvents: TimelineEvent[] = useMemo(
    () => [
      ...planUpdateEvents(timelineSource.history, t, localeTag(language)),
      ...recommendationEvents(
        timelineSource.questions,
        timelineSource.hypotheses,
        timelineSource.experiments,
        t,
        planText,
      ),
    ],
    [timelineSource, t, language, planText],
  )
  const [pendingInquiries, setPendingInquiries] = useState<AthleteInquiry[]>([])
  // Inquiries the athlete has already answered or skipped. A list fetch can be in
  // flight when they deal with one — and a skip changes no chat message, so the
  // effect below is not even re-run to cancel it. Re-pinning a question they just
  // answered is worse than picking up a new one a cycle late.
  const handledInquiryIdsRef = useRef<Set<string>>(new Set())
  // Rephrased questions the athlete has not answered yet, keyed by id. Answering
  // appends chat messages, which re-runs the list effect below — so a fetch
  // carrying the pre-rephrase wording can resolve after the rephrase is applied
  // and roll the pin back to the question the coach already moved on from.
  // handledInquiryIdsRef cannot cover this: a rephrase is still pending, so the
  // question must stay pinned rather than be filtered out.
  const rephrasedInquiriesRef = useRef<Map<string, AthleteInquiry>>(new Map())

  // Pull plan changes and open learning-pipeline recommendations into the timeline.
  // Re-fetched after each exchange since a coach reply may adjust the plan or raise
  // new questions. Failures degrade to no events rather than breaking the chat.
  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    void (async () => {
      const [history, questions, hypotheses, experiments, inquiries] = await Promise.all([
        fetchPlanHistory(authToken).catch(() => [] as PlanDayHistoryEntry[]),
        fetchAthleteOpenQuestions(authToken).catch(() => [] as AthleteOpenQuestion[]),
        fetchAthleteHypotheses(authToken).catch(() => [] as AthleteHypothesis[]),
        fetchValidationExperiments(authToken).catch(() => [] as AthleteExperiment[]),
        fetchAthleteInquiries(authToken).catch(() => [] as AthleteInquiry[]),
      ])
      if (cancelled) return
      setTimelineSource({ history, questions, hypotheses, experiments })
      setPendingInquiries(
        inquiries
          .filter((item) => !handledInquiryIdsRef.current.has(item.id))
          .map((item) => {
            // askCount only ever grows, so it orders the two versions without
            // depending on which request resolved first.
            const local = rephrasedInquiriesRef.current.get(item.id)
            return local && local.askCount > item.askCount ? local : item
          }),
      )
    })()
    return () => {
      cancelled = true
    }
  }, [authToken, chatHistory.length])

  // The oldest unanswered question gets the pin: one question at a time, and the
  // one that has been waiting longest rather than the newest distraction.
  const pinnedInquiry = pendingInquiries[0]

  const handleInquiryAnswer = async (inquiry: AthleteInquiry, answer: string, option?: number) => {
    if (!authToken) return
    const result =
      option === undefined
        ? await answerAthleteInquiry(authToken, inquiry.id, answer)
        : await answerAthleteInquiry(authToken, inquiry.id, answer, option)
    // Mirror what the backend just persisted into the chat, so the exchange stays
    // readable in the timeline after the pin clears.
    const timestamp = new Date().toISOString()
    addChatMessage({ role: 'assistant', content: inquiry.question, timestamp })
    addChatMessage({ role: 'user', content: answer, timestamp })
    if (result.coachReply) {
      addChatMessage({ role: 'assistant', content: result.coachReply, timestamp })
    }
    // A rephrased question comes back still pending and re-pins itself; anything
    // else (answered, or handed off to settings) leaves the pin for good.
    if (result.inquiry.status !== 'pending') {
      handledInquiryIdsRef.current.add(result.inquiry.id)
      rephrasedInquiriesRef.current.delete(result.inquiry.id)
    } else {
      rephrasedInquiriesRef.current.set(result.inquiry.id, result.inquiry)
    }
    setPendingInquiries((current) =>
      result.inquiry.status === 'pending'
        ? current.map((item) => (item.id === result.inquiry.id ? result.inquiry : item))
        : current.filter((item) => item.id !== result.inquiry.id),
    )
    // A tap can change the profile: the starting-volume answer sets the weekly
    // hours (ai-trainer-ops#236). Reloaded, the plan follows at once through
    // usePlanAutoRebuild. Best-effort: the next load brings it anyway.
    if (option !== undefined) {
      fetchCurrentUser(authToken)
        .then((user) => setUserProfile(user.profile))
        .catch(() => {})
    }
    // The card's judge cannot change the plan. An answer that also asks for
    // something, such as a ride tomorrow, goes on to the coach as well
    // (ai-trainer-ops#103 Q28), whether or not it answered the question. It is
    // already in the timeline, so it is neither shown nor stored again.
    if (result.forwardToCoach) await forwardToCoach(answer)
  }

  const handleInquirySkip = async (inquiry: AthleteInquiry) => {
    if (!authToken) return
    await dismissAthleteInquiry(authToken, inquiry.id)
    handledInquiryIdsRef.current.add(inquiry.id)
    rephrasedInquiriesRef.current.delete(inquiry.id)
    setPendingInquiries((current) => current.filter((item) => item.id !== inquiry.id))
  }

  return { timelineEvents, pinnedInquiry, handleInquiryAnswer, handleInquirySkip }
}
