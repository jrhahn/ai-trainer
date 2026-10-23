import { useCallback, useEffect, useState } from 'react'
import { Check, Flag } from 'lucide-react'
import { apiFetch } from '../services/api'
import { localeTag, useLanguage, useT } from '../i18n'

interface WrongFlagEntry {
  id: string
  createdAt: string
  email: string
  target: 'reply' | 'day'
  ref: string
  excerpt: string
  note: string | null
  gitSha: string
  handledAt?: string | null
  handledNote?: string | null
}

/** One flag and the owner's mark that it was answered or became an issue (ai-trainer-ops#96). */
function FlagItem({
  flag,
  onHandled,
}: {
  flag: WrongFlagEntry
  onHandled: (handled: boolean, note: string) => Promise<void>
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const t = useT()
  const language = useLanguage()

  const mark = async (handled: boolean) => {
    setBusy(true)
    try {
      await onHandled(handled, note)
      setNote('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded-xl border border-gray-200 bg-white p-4 text-sm shadow-xs">
      <p className="text-xs text-gray-500">
        {new Date(flag.createdAt).toLocaleString(localeTag(language))} · {flag.email} ·{' '}
        {flag.target === 'reply' ? t('coach reply') : t('planned day')} {flag.ref} · {flag.gitSha.slice(0, 7)}
      </p>
      {flag.note && <p className="mt-1 font-medium text-gray-900">{flag.note}</p>}
      <p className="mt-1 whitespace-pre-wrap text-gray-600">{flag.excerpt}</p>
      {flag.handledAt ? (
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-green-700">
          <Check size={14} aria-hidden="true" />
          {t('Handled {when}', { when: new Date(flag.handledAt).toLocaleString(localeTag(language)) })}
          {flag.handledNote && <span>· {flag.handledNote}</span>}
          <button
            type="button"
            disabled={busy}
            onClick={() => mark(false)}
            className="text-gray-500 underline hover:text-gray-700 disabled:opacity-50"
          >
            {t('Open again')}
          </button>
        </p>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={note}
            maxLength={200}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t('How: answered, or the issue')}
            aria-label={t('How it was handled')}
            className="min-w-0 flex-1 rounded-lg border border-gray-300 px-2 py-1 text-xs"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => mark(true)}
            className="rounded-lg border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {t('Mark handled')}
          </button>
        </div>
      )}
    </li>
  )
}

/** The "this is wrong" flags testers sent, newest first (ai-trainer-ops#94). */
export default function AdminWrongFlags({ token }: { token: string }) {
  const [flags, setFlags] = useState<WrongFlagEntry[] | null>(null)
  const [error, setError] = useState('')
  const t = useT()

  const load = useCallback(
    () =>
      apiFetch<{ flags: WrongFlagEntry[] }>('/admin/wrong-flags', { token })
        .then((data) => setFlags(data.flags))
        .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the flags.')),
    [token],
  )

  useEffect(() => {
    load()
  }, [load])

  const setHandled = async (id: string, handled: boolean, note: string) => {
    setError('')
    try {
      await apiFetch<void>(`/admin/wrong-flags/${id}/handled`, {
        token,
        method: 'PUT',
        body: { handled, note: note.trim() || null },
      })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save that.')
    }
  }

  const open = flags?.filter((flag) => !flag.handledAt).length ?? 0

  return (
    <section className="mt-8">
      <h2 className="mb-3 flex items-center gap-2 text-base font-bold text-gray-900">
        <Flag size={16} aria-hidden="true" /> {t('“This is wrong” flags')}
      </h2>
      {error && <p className="text-sm text-red-600">{t(error)}</p>}
      {flags && flags.length === 0 && <p className="text-sm text-gray-500">{t('No flags yet.')}</p>}
      {flags && flags.length > 0 && (
        <>
          <p className="mb-2 text-sm text-gray-600">{t('{open} of {total} still open', { open, total: flags.length })}</p>
          <ul className="space-y-3">
            {flags.map((flag) => (
              <FlagItem
                key={flag.id}
                flag={flag}
                onHandled={(handled, note) => setHandled(flag.id, handled, note)}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
