import { useState } from 'react'
import { Flag } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { flagWrong } from '../services/user'
import { useT } from '../i18n'

/** What the server keeps of a long reply; it refuses more. */
const EXCERPT_MAX = 4000
const NOTE_MAX = 1000

/** "This is wrong", one tap on a coach reply or a planned day (ai-trainer-ops#94).
 *
 * A tester with little patience does not write an email. The tap is the whole
 * report; the note is optional. What was flagged goes along as the athlete saw
 * it, because a reply's live timestamp and a day the next plan run rewrites
 * cannot find it again later.
 */
export default function WrongFlag({
  target,
  flagRef,
  excerpt,
  tone = 'light',
}: {
  target: 'reply' | 'day'
  /** The reply's timestamp, or the day's date and slot. Not `ref`: React keeps that one. */
  flagRef: string
  excerpt: string
  tone?: 'light' | 'dark'
}) {
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const [state, setState] = useState<'idle' | 'open' | 'sending' | 'sent'>('idle')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const muted = tone === 'dark' ? 'text-slate-400 hover:text-slate-200' : 'text-gray-400 hover:text-gray-600'

  if (!authToken) return null

  if (state === 'sent') {
    return (
      <p role="status" className={`mt-2 text-xs ${tone === 'dark' ? 'text-slate-300' : 'text-gray-500'}`}>
        {t('Thanks. We see what you flagged, and your note.')}
      </p>
    )
  }

  if (state === 'idle') {
    return (
      <button
        type="button"
        onClick={() => setState('open')}
        className={`mt-2 inline-flex items-center gap-1 text-xs ${muted}`}
      >
        <Flag size={12} aria-hidden="true" />
        {t('This is wrong')}
      </button>
    )
  }

  const send = async () => {
    setState('sending')
    setError('')
    try {
      await flagWrong(authToken, { target, ref: flagRef, excerpt: excerpt.slice(0, EXCERPT_MAX), note: note.trim() || undefined })
      setState('sent')
    } catch (e) {
      setError(e instanceof Error ? e.message : t('It could not be sent.'))
      setState('open')
    }
  }

  return (
    <div className="mt-2 space-y-1.5">
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={NOTE_MAX}
        rows={2}
        aria-label={t('What is wrong? (optional)')}
        placeholder={t('What is wrong? (optional)')}
        className="w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-800"
      />
      {error && (
        <p role="alert" className="text-xs text-red-600">
          {t(error)}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void send()}
          disabled={state === 'sending'}
          className="rounded-lg bg-amber-500 px-2.5 py-1 text-xs font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
        >
          {t('Send')}
        </button>
        <button type="button" onClick={() => setState('idle')} className={`text-xs ${muted}`}>
          {t('Cancel')}
        </button>
      </div>
    </div>
  )
}
