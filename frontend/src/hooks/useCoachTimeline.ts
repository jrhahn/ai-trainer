import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { setProfile } from '../queries/profile'
import { answerAthleteInquiry, dismissAthleteInquiry, fetchCoachTimeline, fetchCurrentUser } from '../services/user'
import type { AthleteInquiry, CoachTimeline } from '../services/user'
import { planUpdateEvents, recommendationEvents } from '../utils/coachTimeline'
import type { TimelineEvent } from '../utils/coachTimeline'
import { localeTag, useLanguage, useT } from '../i18n'
import { usePlanText, usePlanTextPending } from './usePlanTranslations'
import { COACH_TIMELINE_QUERY_KEY } from '../queries/keys'

// Kept between two chats for a while; an exchange fetches it again at once.
const TIMELINE_STALE_MS = 5 * 60 * 1000
const NO_TIMELINE: CoachTimeline = { history: [], questions: [], hypotheses: [], experiments: [], inquiries: [] }

/** The coach timeline's plan changes and recommendations, and the question
 *  pinned above it, fetched again after each exchange (#418, #506). An answer
 *  that also asks for something goes on through `forwardToCoach`.
 *  Out of `AIChat` (ai-trainer-ops#274); a query since ai-trainer-ops#277. */
export function useCoachTimeline(forwardToCoach: (answer: string) => Promise<void>) {
  const t = useT()
  const planText = usePlanText()
  const isPending = usePlanTextPending()
  const queryClient = useQueryClient()
  const { authToken, addChatMessage } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      addChatMessage: s.addChatMessage,
    }))
  )

  // Pull plan changes and open learning-pipeline recommendations into the
  // timeline, with the questions to pin. Fetched again after each exchange, as a
  // coach reply may adjust the plan or raise new questions: the send and the
  // question card invalidate it, once per exchange. It used to follow the chat's
  // length, which changes for the message and again for the reply, so every
  // message fetched the five lists twice (ai-trainer-ops#277). Failures degrade
  // to no events rather than breaking the chat.
  const { data } = useQuery({
    queryKey: [COACH_TIMELINE_QUERY_KEY, authToken],
    // One request since ai-trainer-ops#277; it was five, one per list.
    queryFn: () => fetchCoachTimeline(authToken!).catch((): CoachTimeline => NO_TIMELINE),
    enabled: !!authToken,
    staleTime: TIMELINE_STALE_MS,
    refetchOnWindowFocus: false,
  })
  const refreshTimeline = () => void queryClient.invalidateQueries({ queryKey: [COACH_TIMELINE_QUERY_KEY] })

  // The raw lists, not their sentences: the sentences follow the interface
  // language, which can change while the chat is open (ai-trainer-ops#103 Q27).
  const language = useLanguage()
  const timelineEvents: TimelineEvent[] = useMemo(
    () =>
      data
        ? [
            ...planUpdateEvents(data.history, t, localeTag(language), planText),
            ...recommendationEvents(data.questions, data.hypotheses, data.experiments, t, planText, isPending),
          ]
        : [],
    [data, t, language, planText, isPending],
  )
  // Inquiries the athlete has already answered or skipped. A list fetch can be in
  // flight when they deal with one. Re-pinning a question they just answered is
  // worse than picking up a new one a cycle late.
  const [handledInquiryIds, setHandledInquiryIds] = useState<ReadonlySet<string>>(() => new Set())
  // Rephrased questions the athlete has not answered yet, keyed by id. A fetch
  // carrying the pre-rephrase wording can resolve after the rephrase is applied
  // and roll the pin back to the question the coach already moved on from.
  // handledInquiryIds cannot cover this: a rephrase is still pending, so the
  // question must stay pinned rather than be filtered out.
  const [rephrasedInquiries, setRephrasedInquiries] = useState<ReadonlyMap<string, AthleteInquiry>>(() => new Map())
  const pendingInquiries = (data?.inquiries ?? [])
    .filter((item) => !handledInquiryIds.has(item.id))
    .map((item) => {
      // askCount only ever grows, so it orders the two versions without
      // depending on which request resolved first.
      const local = rephrasedInquiries.get(item.id)
      return local && local.askCount > item.askCount ? local : item
    })

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
      setHandledInquiryIds((ids) => new Set(ids).add(result.inquiry.id))
      setRephrasedInquiries((map) => withoutKey(map, result.inquiry.id))
    } else {
      setRephrasedInquiries((map) => new Map(map).set(result.inquiry.id, result.inquiry))
    }
    // The answer may have raised the next question or changed the plan.
    refreshTimeline()
    // A tap can change the profile: the starting-volume answer sets the weekly
    // hours (ai-trainer-ops#236). Reloaded, the plan follows at once through
    // usePlanAutoRebuild. Best-effort: the next load brings it anyway.
    if (option !== undefined) {
      fetchCurrentUser(authToken)
        .then((user) => setProfile(user.profile))
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
    setHandledInquiryIds((ids) => new Set(ids).add(inquiry.id))
    setRephrasedInquiries((map) => withoutKey(map, inquiry.id))
  }

  return { timelineEvents, pinnedInquiry, handleInquiryAnswer, handleInquirySkip }
}

function withoutKey<K, V>(map: ReadonlyMap<K, V>, key: K): ReadonlyMap<K, V> {
  const next = new Map(map)
  next.delete(key)
  return next
}
