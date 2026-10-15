import { useEffect, useState } from 'react'
import { Flag } from 'lucide-react'
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
}

/** The "this is wrong" flags testers sent, newest first (ai-trainer-ops#94). */
export default function AdminWrongFlags({ token }: { token: string }) {
  const [flags, setFlags] = useState<WrongFlagEntry[] | null>(null)
  const [error, setError] = useState('')
  const t = useT()
  const language = useLanguage()

  useEffect(() => {
    apiFetch<{ flags: WrongFlagEntry[] }>('/admin/wrong-flags', { token })
      .then((data) => setFlags(data.flags))
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the flags.'))
  }, [token])

  return (
    <section className="mt-8">
      <h2 className="mb-3 flex items-center gap-2 text-base font-bold text-gray-900">
        <Flag size={16} aria-hidden="true" /> {t('“This is wrong” flags')}
      </h2>
      {error && <p className="text-sm text-red-600">{t(error)}</p>}
      {flags && flags.length === 0 && <p className="text-sm text-gray-500">{t('No flags yet.')}</p>}
      {flags && flags.length > 0 && (
        <ul className="space-y-3">
          {flags.map((flag) => (
            <li key={flag.id} className="rounded-xl border border-gray-200 bg-white p-4 text-sm shadow-xs">
              <p className="text-xs text-gray-500">
                {new Date(flag.createdAt).toLocaleString(localeTag(language))} · {flag.email} ·{' '}
                {flag.target === 'reply' ? t('coach reply') : t('planned day')} {flag.ref} · {flag.gitSha.slice(0, 7)}
              </p>
              {flag.note && <p className="mt-1 font-medium text-gray-900">{flag.note}</p>}
              <p className="mt-1 whitespace-pre-wrap text-gray-600">{flag.excerpt}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
