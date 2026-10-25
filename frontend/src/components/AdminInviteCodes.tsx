import { useEffect, useState } from 'react'
import { Copy, Ticket, Trash2 } from 'lucide-react'
import { apiFetch } from '../services/api'
import { inviteLink } from '../utils/links'
import { localeTag, useLanguage, useT } from '../i18n'

interface InviteCode {
  id: string
  code: string
  note: string | null
  createdAt: string
  usedAt: string | null
}

interface InviteCodesResponse {
  codes: InviteCode[]
  inviteOnly: boolean
}

/** Single-use invitations for a closed round (ops#108 option (b), #96). */
export default function AdminInviteCodes({ token }: { token: string }) {
  const t = useT()
  const language = useLanguage()
  const [data, setData] = useState<InviteCodesResponse | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [count, setCount] = useState(1)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<InviteCodesResponse>('/admin/invite-codes', { token })
      .then((d) => setData({ codes: d?.codes ?? [], inviteOnly: Boolean(d?.inviteOnly) }))
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the invitations.'))
  }, [token])

  const create = async () => {
    setBusy(true)
    setError('')
    try {
      setData(
        await apiFetch<InviteCodesResponse>('/admin/invite-codes', {
          token,
          method: 'POST',
          body: { count, note: note.trim() || null },
        }),
      )
      setNote('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the invitations.')
    } finally {
      setBusy(false)
    }
  }

  const revoke = async (id: string) => {
    setError('')
    try {
      await apiFetch<void>(`/admin/invite-codes/${id}`, { token, method: 'DELETE' })
      setData((d) => (d ? { ...d, codes: d.codes.filter((c) => c.id !== id) } : d))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not withdraw the invitation.')
    }
  }

  const copy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(inviteLink(code))
      setCopied(code)
    } catch {
      setError('Could not copy the link. Select it and copy it by hand.')
    }
  }

  return (
    <section className="mt-8">
      <h2 className="mb-3 flex items-center gap-2 text-base font-bold text-gray-900">
        <Ticket size={16} aria-hidden="true" /> {t('Invitations')}
      </h2>
      {data && !data.inviteOnly && (
        <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {t('Sign-up is open on this server, so nobody needs a code. Set SIGNUP_INVITE_ONLY to make it a closed round.')}
        </p>
      )}
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-xs text-gray-600">
          {t('For whom (only you see this)')}
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={100}
            className="mt-1 w-56 rounded-lg border border-gray-300 px-3 py-2 text-sm"
            placeholder={t('e.g. Anna')}
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600">
          {t('How many')}
          <input
            type="number"
            min={1}
            max={20}
            value={count}
            onChange={(e) => setCount(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
            className="mt-1 w-20 rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
        </label>
        <button
          type="button"
          onClick={create}
          disabled={busy}
          className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-60"
        >
          {t('Create invitation')}
        </button>
      </div>
      {error && <p role="alert" className="mb-2 text-sm text-red-600">{t(error)}</p>}
      {data && data.codes.length === 0 && <p className="text-sm text-gray-500">{t('No invitations yet.')}</p>}
      {data && data.codes.length > 0 && (
        <ul className="space-y-2">
          {data.codes.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-3 text-sm">
              <code className="font-mono font-semibold tracking-wider">{c.code}</code>
              {c.note && <span className="text-gray-700">{c.note}</span>}
              <span className="text-xs text-gray-500">
                {c.usedAt
                  ? t('Used {when}', { when: new Date(c.usedAt).toLocaleString(localeTag(language)) })
                  : t('Not used yet')}
              </span>
              {!c.usedAt && (
                <span className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => copy(c.code)}
                    className="inline-flex items-center gap-1 rounded-lg border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
                  >
                    <Copy size={12} aria-hidden="true" />
                    {copied === c.code ? t('Link copied') : t('Copy link')}
                  </button>
                  <button
                    type="button"
                    onClick={() => revoke(c.id)}
                    aria-label={t('Withdraw invitation {code}', { code: c.code })}
                    className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                  >
                    <Trash2 size={12} aria-hidden="true" />
                    {t('Withdraw')}
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
