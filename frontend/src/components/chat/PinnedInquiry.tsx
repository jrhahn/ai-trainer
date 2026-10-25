import { useState } from 'react'
import type { AthleteInquiry } from '../../services/user'
import { useT } from '../../i18n'
import { enterSends } from '../../utils/enterSends'
import { button, chip, input as inputClass, note, textButton } from '../ui'

// A question the coach could never answer from data, pinned above the feed until
// the athlete deals with it (#506). It sits outside the scrolling feed on purpose:
// as an ordinary chat message it would be buried by the next exchange, which is
// exactly how these questions went unanswered before.
//
// Out of `AIChat` (ai-trainer-ops#274).
export default function PinnedInquiry({
  inquiry,
  onAnswer,
  onSkip,
}: {
  inquiry: AthleteInquiry
  onAnswer: (answer: string, option?: number) => Promise<void>
  onSkip: () => Promise<void>
}) {
  const t = useT()
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // A fresh question after a rephrasing must not inherit the text that missed.
  // Reset while rendering, not in an effect (ai-trainer-ops#286).
  const [shownQuestion, setShownQuestion] = useState({ id: inquiry.id, question: inquiry.question })
  if (shownQuestion.id !== inquiry.id || shownQuestion.question !== inquiry.question) {
    setShownQuestion({ id: inquiry.id, question: inquiry.question })
    setAnswer('')
    setError('')
  }

  const submit = async () => {
    if (!answer.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      await onAnswer(answer.trim())
      setAnswer('')
    } catch {
      setError("Couldn't send that answer — try again.")
    } finally {
      setBusy(false)
    }
  }

  // One tap answers (ai-trainer-ops#155). The index is what the server reads;
  // the label is what the chat shows.
  const tap = async (label: string, option: number) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await onAnswer(label, option)
    } catch {
      setError("Couldn't send that answer — try again.")
    } finally {
      setBusy(false)
    }
  }

  // No in-flight guard here: both buttons carry `disabled={busy}`, so a second
  // click cannot reach this. `submit` still needs its own check because Enter can
  // fire on an empty box.
  const skip = async () => {
    setBusy(true)
    try {
      await onSkip()
    } catch {
      setError("Couldn't skip that — try again.")
    } finally {
      setBusy(false)
    }
  }

  // Set apart by a rule and its title, not by a yellow box (owner's review,
  // 2026-10-24: no tinted boxes).
  return (
    <div data-testid="pinned-inquiry" className="border-b border-gray-100 px-4 py-3 text-sm">
      <p className="font-semibold text-gray-900">
        {inquiry.askCount > 1 ? t('Still needs an answer') : t('Your coach needs an answer')}
      </p>
      <p className="mt-1 text-gray-800">{inquiry.question}</p>
      {inquiry.whyAsking && <p className="mt-1 text-gray-500">{inquiry.whyAsking}</p>}
      {inquiry.options && inquiry.options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label={t('Answers to tap')}>
          {inquiry.options.map((label, index) => (
            <button
              key={`${index}-${label}`}
              type="button"
              onClick={() => void tap(label, index)}
              disabled={busy}
              className={`${chip(false)} disabled:opacity-50`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      <textarea
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && enterSends()) {
            e.preventDefault()
            void submit()
          }
        }}
        placeholder={t('Type your answer...')}
        aria-label={t('Answer: {question}', { question: inquiry.question })}
        rows={2}
        disabled={busy}
        className={`mt-2 resize-none disabled:opacity-50 ${inputClass}`}
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button onClick={() => void submit()} disabled={busy || !answer.trim()} className={button}>
          {t('Send')}
        </button>
        <button onClick={() => void skip()} disabled={busy} className={textButton}>
          {t('Skip for now')}
        </button>
        {inquiry.settingsHint && <span className="ml-auto text-gray-500">{inquiry.settingsHint}</span>}
      </div>
      {error && <p className={`mt-1 ${note(false)}`}>{t(error)}</p>}
    </div>
  )
}
