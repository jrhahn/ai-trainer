import { useState } from 'react'
import { useAppStore } from '../store/useAppStore'
import { flagWrong } from '../services/user'
import { useT } from '../i18n'
import { button, input, note, textButton } from './ui'

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
}: {
  target: 'reply' | 'day'
  /** The reply's timestamp, or the day's date and slot. Not `ref`: React keeps that one. */
  flagRef: string
  excerpt: string
}) {
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const [state, setState] = useState<'idle' | 'open' | 'sending' | 'sent'>('idle')
  const [noteText, setNoteText] = useState('')
  const [error, setError] = useState('')
  const [sentWithNote, setSentWithNote] = useState(false)

  if (!authToken) return null

  if (state === 'sent') {
    return (
      <p role="status" className={`mt-2 ${note(true)}`}>
        {sentWithNote ? t('Thanks. We see what you flagged, and your note.') : t('Thanks. We see what you flagged.')}
      </p>
    )
  }

  if (state === 'idle') {
    return (
      <button type="button" onClick={() => setState('open')} className={`mt-2 block ${textButton}`}>
        {t('This is wrong')}
      </button>
    )
  }

  const send = async () => {
    setState('sending')
    setError('')
    try {
      await flagWrong(authToken, { target, ref: flagRef, excerpt: excerpt.slice(0, EXCERPT_MAX), note: noteText.trim() || undefined })
      setSentWithNote(noteText.trim() !== '')
      setState('sent')
    } catch (e) {
      setError(e instanceof Error ? e.message : t('It could not be sent.'))
      setState('open')
    }
  }

  return (
    <div className="mt-2 space-y-2">
      <textarea
        value={noteText}
        onChange={(e) => setNoteText(e.target.value)}
        maxLength={NOTE_MAX}
        rows={2}
        aria-label={t('What is wrong? (optional)')}
        placeholder={t('What is wrong? (optional)')}
        className={input}
      />
      {error && (
        <p role="alert" className={note(false)}>
          {t(error)}
        </p>
      )}
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => void send()} disabled={state === 'sending'} className={button}>
          {t('Send')}
        </button>
        <button type="button" onClick={() => setState('idle')} className={textButton}>
          {t('Cancel')}
        </button>
      </div>
    </div>
  )
}
