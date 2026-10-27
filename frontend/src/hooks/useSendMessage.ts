import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { setProfile, useProfile } from '../queries/profile'
import type { TrainingDay } from '../store/useAppStore'
import { askTrainer, refreshLoginSummary } from '../services/ai'
import { fetchCoachMemory, fetchCurrentUser } from '../services/user'
import { alreadyRecorded, parseReportedSession } from '../utils/reportedSession'
import { useT } from '../i18n'
import { COACH_TIMELINE_QUERY_KEY } from '../queries/keys'
import { setPlan } from '../queries/plan'

/** After this long the dots say the coach is still at it, and offer to stop
 *  waiting (ai-trainer-ops#267): a reply waited forever until the proxy gave up. */
const SLOW_REPLY_MS = 20_000

/** What the coach chat sends: the message box, the request with its slow-reply
 *  notice and its stop, the retry after a failure, and a message the app sends
 *  in the athlete's name. Out of `AIChat` (ai-trainer-ops#274). */
export function useSendMessage(contextWorkout: TrainingDay | undefined) {
  const t = useT()
  const queryClient = useQueryClient()
  const userProfile = useProfile()
  const {
    authToken,
    addChatMessage,
    setCoachMemory,
    showWarning,
    updateRideMetricLabel,
    pendingCoachMessage,
    setPendingCoachMessage,
    setRiderAssessment,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      addChatMessage: s.addChatMessage,
      setCoachMemory: s.setCoachMemory,
      showWarning: s.showWarning,
      updateRideMetricLabel: s.updateRideMetricLabel,
      pendingCoachMessage: s.pendingCoachMessage,
      setPendingCoachMessage: s.setPendingCoachMessage,
      setRiderAssessment: s.setRiderAssessment,
    }))
  )

  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [slowReply, setSlowReply] = useState(false)
  const askAbortRef = useRef<AbortController | null>(null)
  const [lastFailedMessage, setLastFailedMessage] = useState<string | null>(null)
  const [staleRefreshWarning, setStaleRefreshWarning] = useState(false)
  const sendInFlightRef = useRef(false)
  // A question-card answer to send on to the coach once the chat request in
  // flight has finished (ai-trainer-ops#103 Q28): sendMessage refuses a second
  // send while one runs, and the forward must not be lost to that.
  const pendingForwardRef = useRef<string | null>(null)

  /** A session the athlete just reported, unless a device already brought it
   *  in (ai-trainer-ops#166). */
  function offerToEnter(text: string) {
    const reported = parseReportedSession(text)
    if (!reported || alreadyRecorded(reported, useAppStore.getState().rideMetricsHistory)) {
      return undefined
    }
    return reported
  }

  async function sendMessage(
    msgOverride?: string,
    options?: { skipAddUserMessage?: boolean; alreadyInChat?: boolean },
  ) {
    const raw = typeof msgOverride === 'string' ? msgOverride : input
    // The ref, not `loading`: a closure from an earlier render can still see
    // `loading` true after the send it belonged to has finished.
    if (!raw.trim() || sendInFlightRef.current) return

    const userMsg = raw.trim()
    const timestamp = new Date().toISOString()
    // Only clear the textarea when sending what was typed in it
    if (typeof msgOverride !== 'string') setInput('')

    if (!userProfile || !authToken) return

    if (!options?.skipAddUserMessage) {
      addChatMessage({ role: 'user', content: userMsg, timestamp })
    }
    sendInFlightRef.current = true
    setLoading(true)
    const controller = new AbortController()
    askAbortRef.current = controller
    const slowTimer = setTimeout(() => setSlowReply(true), SLOW_REPLY_MS)

    try {
      const result = await askTrainer(userMsg, authToken, {
        contextWorkout,
        alreadyInChat: options?.alreadyInChat,
        signal: controller.signal,
      })

      // The server applies the updates and answers with the plan it stored; it
      // sends `updatedPlan` whenever it applied any. Updates without it were not
      // stored, so they are not shown either (ai-trainer-ops#275).
      let planUpdateCount = 0
      if (result.updatedPlan) {
        setPlan(result.updatedPlan)
        planUpdateCount = (result.planUpdates ?? []).length
      }
      // The backend drops the training summary whenever the plan changes, but the
      // dashboard asks for a new one only when it loads. Without this the card
      // kept the old plan ("Friday: rest day") beside the changed week. Before
      // the first analysed ride there is no assessment and so no summary; the
      // request was a 404 that still spent the AI allowance (ai-trainer-ops#183).
      if (planUpdateCount > 0 && useAppStore.getState().riderAssessment) {
        refreshLoginSummary(authToken)
          .then((loginSummary) => {
            const current = useAppStore.getState().riderAssessment
            if (loginSummary && current) setRiderAssessment({ ...current, loginSummary })
          })
          // The old summary stays until the next dashboard load regenerates it, and
          // the athlete hears so (ai-trainer-ops#272).
          .catch(() =>
            showWarning(t('The training summary could not be updated after this change. It updates the next time the dashboard loads.')),
          )
      }

      if (result.rideLabelUpdates && result.rideLabelUpdates.length > 0) {
        for (const labelUpdate of result.rideLabelUpdates) {
          updateRideMetricLabel(labelUpdate.stravaActivityId, labelUpdate.labelOverride)
        }
      }

      addChatMessage({
        role: 'assistant',
        content: result.response,
        timestamp: new Date().toISOString(),
        planUpdateCount: planUpdateCount > 0 ? planUpdateCount : undefined,
        sources: result.sources?.length ? result.sources : undefined,
        physiologyRationale: result.physiologyRationale || undefined,
        contextRationale: result.contextRationale || undefined,
        objectiveRationale: result.objectiveRationale || undefined,
        capturedGoals: result.capturedGoals?.length ? result.capturedGoals : undefined,
        capturedStates: result.capturedStates?.length ? result.capturedStates : undefined,
        capturedConstraints: result.capturedConstraints?.length
          ? result.capturedConstraints
          : undefined,
        capturedDayPreferences: result.capturedDayPreferences?.length
          ? result.capturedDayPreferences
          : undefined,
        coachSaw: result.coachSaw?.length ? result.coachSaw : undefined,
        notes: result.notes?.length ? result.notes : undefined,
        daySummaries: result.daySummaries?.length ? result.daySummaries : undefined,
        raceProposal: result.raceProposal ?? undefined,
        raceOffer: result.raceOffer ?? undefined,
        profileProposal: result.profileProposal ?? undefined,
        reportedSession: offerToEnter(userMsg),
      })
      setLastFailedMessage(null)
      // The reply may have changed the plan or raised questions: the timeline's
      // lists once per exchange (ai-trainer-ops#277).
      void queryClient.invalidateQueries({ queryKey: [COACH_TIMELINE_QUERY_KEY] })

      // Re-sync coach memory and profile from server (backend updated both inside ask_trainer).
      // Retry once on transient failure; show a warning if the refresh still can't complete.
      const retry = <T,>(fn: () => Promise<T>) => fn().catch(() => fn())
      const refreshResults = await Promise.allSettled([
        retry(() => fetchCoachMemory(authToken).then((m) => setCoachMemory(m))),
        retry(() => fetchCurrentUser(authToken).then((u) => setProfile(u.profile))),
      ])
      if (refreshResults.some((r) => r.status === 'rejected')) {
        setStaleRefreshWarning(true)
      } else {
        setStaleRefreshWarning(false)
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // The athlete stopped waiting. The server may still answer and store the
        // reply, so no retry, which could ask the same question twice.
        addChatMessage({
          role: 'assistant',
          content: t('Stopped waiting. If your coach answers after all, the reply is here the next time the page loads.'),
          timestamp: new Date().toISOString(),
        })
        return
      }
      setLastFailedMessage(userMsg)
      // An exhausted free allowance is not a failure to apologise for: the
      // server says what to do about it (ai-trainer-ops#79). Any other failure
      // says what happened too: the backend and the client word every one of
      // them now (ai-trainer-ops#95).
      const allowanceSpent = (err as { status?: number } | null)?.status === 402
      const reason = err instanceof Error ? err.message : ''
      // In the interface's language, and the place named as the page names it
      // (ai-trainer-ops#121). The server's reason is translated when de.ts
      // knows it; one with numbers in it stays as the server wrote it.
      const translatedReason = reason ? t(reason) : ''
      addChatMessage({
        role: 'assistant',
        content: allowanceSpent
          ? `${translatedReason}\n\n${t('Coach & AI')} → ${t('Your AI Provider Key')}.`
          : `${t('Sorry, I could not answer.')} ${translatedReason}`.trim(),
        timestamp: new Date().toISOString(),
        failedUserMessage: userMsg,
      })
    } finally {
      clearTimeout(slowTimer)
      setSlowReply(false)
      askAbortRef.current = null
      sendInFlightRef.current = false
      setLoading(false)
      const forward = pendingForwardRef.current
      if (forward) {
        pendingForwardRef.current = null
        void sendMessage(forward, { skipAddUserMessage: true, alreadyInChat: true })
      }
    }
  }

  // An effect event, so the effect below runs the latest sendMessage without a
  // ref written during render (ai-trainer-ops#286).
  const sendPending = useEffectEvent((msg: string) => void sendMessage(msg))

  // Auto-send a message triggered externally (e.g. match-score badge click)
  useEffect(() => {
    if (!pendingCoachMessage) return
    setPendingCoachMessage(null)
    sendPending(pendingCoachMessage)
  }, [pendingCoachMessage, setPendingCoachMessage])

  // A question-card answer that also asks for something goes on to the coach
  // (ai-trainer-ops#103 Q28). With a send in flight it waits for that one.
  const forwardToCoach = async (answer: string) => {
    if (sendInFlightRef.current) {
      pendingForwardRef.current = answer
    } else {
      await sendMessage(answer, { skipAddUserMessage: true, alreadyInChat: true })
    }
  }

  return {
    input,
    setInput,
    loading,
    slowReply,
    stopWaiting: () => askAbortRef.current?.abort(),
    lastFailedMessage,
    staleRefreshWarning,
    sendMessage,
    forwardToCoach,
  }
}
