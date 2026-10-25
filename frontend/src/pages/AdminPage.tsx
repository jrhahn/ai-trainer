import { useEffect, useState } from 'react'
import { Activity, Check, Hourglass, Loader2, LogOut, Shield, Trash2, Users, Zap } from 'lucide-react'
import { apiFetch } from '../services/api'
import AdminInviteCodes from '../components/AdminInviteCodes'
import AdminWrongFlags from '../components/AdminWrongFlags'
import { localeTag, slots, useLanguage, useT, type Language, type Translate } from '../i18n'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AdminUserStat {
  id: string
  email: string
  name: string | null
  createdAt: string
  lastLogin: string | null
  aiProvider: string
  isOnboarded: boolean
  stravaConnected: boolean
  stravaAnalysisComplete: boolean
  consumedTokens: number
  rideCount: number
  lastActivityDate: string | null
  chatMessageCount: number
}

interface AdminUsersResponse {
  users: AdminUserStat[]
  totalUsers: number
  totalTokens: number
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number, language: Language): string {
  return n.toLocaleString(localeTag(language))
}

function relativeDate(iso: string, t: Translate): string {
  const d = new Date(iso)
  const diff = Date.now() - d.getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return t('today')
  if (days === 1) return t('yesterday')
  if (days < 30) return t('{n}d ago', { n: days })
  if (days < 365) return t('{n}mo ago', { n: Math.floor(days / 30) })
  return t('{n}y ago', { n: Math.floor(days / 365) })
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  const language = useLanguage()
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-5 py-4 shadow-xs flex items-center gap-4">
      <div className="text-blue-500">{icon}</div>
      <div>
        <p className="text-xs text-gray-400 uppercase tracking-wider font-semibold">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{fmt(value, language)}</p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const t = useT()
  const language = useLanguage()
  const [password, setPassword] = useState('')
  // Asked for only when the server says a second factor is configured (#688).
  // Rendering it unconditionally would prompt for a code on deployments that
  // have none, and failing a correct password for a missing field is worse
  // than one extra request on the login screen.
  const [adminCode, setAdminCode] = useState('')
  const [totpRequired, setTotpRequired] = useState(false)
  const [adminToken, setAdminToken] = useState<string | null>(null)
  const [loginError, setLoginError] = useState<string | null>(null)

  // One unauthenticated call on mount to learn whether to show the code field.
  // It leaks only that a second factor exists, which any attempt reveals
  // anyway. A failure leaves it off: the server is the one that enforces this,
  // and a hidden field cannot let a wrong login through.
  useEffect(() => {
    apiFetch<{ required?: boolean }>('/admin/totp-required')
      .then((data) => setTotpRequired(!!data.required))
      .catch(() => setTotpRequired(false))
  }, [])
  const [loginLoading, setLoginLoading] = useState(false)

  const [stats, setStats] = useState<AdminUsersResponse | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [statsError, setStatsError] = useState<string | null>(null)

  const [sortKey, setSortKey] = useState<keyof AdminUserStat>('createdAt')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const [deleteTarget, setDeleteTarget] = useState<AdminUserStat | null>(null)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [revokingUserId, setRevokingUserId] = useState<string | null>(null)
  const [revokeError, setRevokeError] = useState<string | null>(null)

  // -------------------------------------------------------------------------
  // Login
  // -------------------------------------------------------------------------

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginError(null)
    setLoginLoading(true)
    try {
      const data = await apiFetch<{ access_token: string }>('/admin/login', {
        method: 'POST',
        body: totpRequired ? { password, code: adminCode } : { password },
      })
      const token = data.access_token
      setAdminToken(token)
      setPassword('')
      // Immediately load stats
      await loadStats(token)
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoginLoading(false)
    }
  }

  // -------------------------------------------------------------------------
  // Stats
  // -------------------------------------------------------------------------

  const loadStats = async (token: string) => {
    setStatsLoading(true)
    setStatsError(null)
    try {
      const data = await apiFetch<AdminUsersResponse>('/admin/users', { token })
      setStats(data)
    } catch (err) {
      setStatsError(err instanceof Error ? err.message : 'Failed to load stats')
    } finally {
      setStatsLoading(false)
    }
  }

  // -------------------------------------------------------------------------
  // Delete user
  // -------------------------------------------------------------------------

  const openDeleteModal = (user: AdminUserStat) => {
    setDeleteTarget(user)
    setDeleteConfirmText('')
    setDeleteError(null)
  }

  const closeDeleteModal = () => {
    if (deleteLoading) return
    setDeleteTarget(null)
    setDeleteConfirmText('')
    setDeleteError(null)
  }

  const handleDelete = async () => {
    if (!deleteTarget || !adminToken) return
    setDeleteLoading(true)
    setDeleteError(null)
    try {
      await apiFetch(`/admin/users/${deleteTarget.id}`, { token: adminToken, method: 'DELETE' })
      setDeleteTarget(null)
      setDeleteConfirmText('')
      // Refresh stats
      await loadStats(adminToken)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setDeleteLoading(false)
    }
  }

  // -------------------------------------------------------------------------
  // Revoke an athlete's sessions (#704)
  //
  // The operator's half of revocation. The athlete's own button needs their
  // password, which is no help in the cases that bring someone here: a token
  // reported leaked by somebody who cannot sign in, or a password changed
  // directly in Authelia's user store — which the app's tokens know nothing
  // about, so every one of them would otherwise stay valid for a week.
  // -------------------------------------------------------------------------

  const handleRevokeSessions = async (user: AdminUserStat) => {
    if (!adminToken) return
    // A confirm() rather than a modal: unlike deletion this is recoverable —
    // the athlete signs in again — so the dialog is here to stop a misclick on
    // the wrong row, not to make anyone think twice.
    if (!window.confirm(t('Sign {email} out of every device?', { email: user.email }))) return
    setRevokingUserId(user.id)
    setRevokeError(null)
    try {
      await apiFetch(`/admin/users/${user.id}/revoke-sessions`, { token: adminToken, method: 'POST' })
    } catch (err) {
      setRevokeError(err instanceof Error ? err.message : 'Could not revoke sessions')
    } finally {
      setRevokingUserId(null)
    }
  }

  // -------------------------------------------------------------------------
  // Table sorting
  // -------------------------------------------------------------------------

  const toggleSort = (key: keyof AdminUserStat) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sortedUsers = stats
    ? [...stats.users].sort((a, b) => {
        const av = a[sortKey] ?? ''
        const bv = b[sortKey] ?? ''
        const cmp = av < bv ? -1 : av > bv ? 1 : 0
        return sortDir === 'asc' ? cmp : -cmp
      })
    : []

  const SortTh = ({ label, field }: { label: string; field: keyof AdminUserStat }) => (
    <th
      className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-800 whitespace-nowrap"
      onClick={() => toggleSort(field)}
    >
      {label}
      {sortKey === field && (
        <span className="ml-1 text-blue-500">{sortDir === 'asc' ? '▲' : '▼'}</span>
      )}
    </th>
  )

  // -------------------------------------------------------------------------
  // Render — login screen
  // -------------------------------------------------------------------------

  if (!adminToken) {
    return (
      <div className="min-h-screen bg-[#0f1116] flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-8">
          <div className="flex items-center gap-2 mb-6">
            <div className="bg-blue-600 rounded-lg p-1.5">
              <Shield size={22} className="text-white" />
            </div>
            <span className="font-bold text-xl text-gray-900">{t('Admin Panel')}</span>
          </div>

          <form onSubmit={(e) => { void handleLogin(e) }} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('Password')}</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-blue-500"
                placeholder={t('Admin password')}
                required
                autoFocus
              />
            </div>
            {totpRequired && (
              <div>
                <label
                  htmlFor="admin-totp-code"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t('Two-factor code')}
                </label>
                <input
                  id="admin-totp-code"
                  type="text"
                  value={adminCode}
                  onChange={(e) => setAdminCode(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg font-mono tracking-widest text-center focus:outline-hidden focus:ring-2 focus:ring-blue-500"
                  placeholder="000000"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                />
              </div>
            )}
            {loginError && (
              <p className="text-sm text-red-600">{t(loginError)}</p>
            )}
            <button
              type="submit"
              disabled={loginLoading}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-lg text-sm transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
            >
              {loginLoading && <Loader2 size={16} className="animate-spin" />}
              {t('Sign In')}
            </button>
          </form>
        </div>
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Render — dashboard
  // -------------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      {/* Header */}
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2">
            <div className="bg-blue-600 rounded-lg p-1.5">
              <Shield size={20} className="text-white" />
            </div>
            <h1 className="text-xl font-bold text-gray-900">{t('Admin Panel')}</h1>
          </div>
          <button
            onClick={() => { setAdminToken(null); setStats(null) }}
            className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <LogOut size={15} />
            {t('Sign out')}
          </button>
        </div>

        {/* Summary cards */}
        {stats && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
            <StatCard label={t('Total users')} value={stats.totalUsers} icon={<Users size={22} />} />
            <StatCard label={t('Total tokens')} value={stats.totalTokens} icon={<Zap size={22} />} />
            <StatCard
              label={t('Strava connected')}
              value={stats.users.filter((u) => u.stravaConnected).length}
              icon={<Activity size={22} />}
            />
          </div>
        )}

        {/* Error / loading */}
        {revokeError && (
          <p className="text-sm text-red-600 mb-4">{t(revokeError)}</p>
        )}
        {statsError && (
          <p className="text-sm text-red-600 mb-4">{t(statsError)}</p>
        )}
        {statsLoading && (
          <div className="flex items-center gap-2 text-gray-500 text-sm mb-4">
            <Loader2 size={16} className="animate-spin" /> {t('Loading…')}
          </div>
        )}

        {/* Table */}
        {stats && (
          <div className="bg-white rounded-xl border border-gray-200 shadow-xs overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <SortTh label={t('Name / Email')} field="email" />
                  <SortTh label={t('Joined')} field="createdAt" />
                  <SortTh label={t('Last login')} field="lastLogin" />
                  <SortTh label={t('Provider')} field="aiProvider" />
                  <SortTh label={t('Rides')} field="rideCount" />
                  <SortTh label={t('Last ride')} field="lastActivityDate" />
                  <SortTh label={t('Chats')} field="chatMessageCount" />
                  <SortTh label={t('Tokens')} field="consumedTokens" />
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">Strava</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sortedUsers.map((u) => (
                  <tr key={u.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-3 py-2.5">
                      <p className="font-medium text-gray-900 truncate max-w-[180px]">{u.name ?? '—'}</p>
                      <p className="text-xs text-gray-400 truncate max-w-[180px]">{u.email}</p>
                    </td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">{relativeDate(u.createdAt, t)}</td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">{u.lastLogin ? relativeDate(u.lastLogin, t) : '—'}</td>
                    <td className="px-3 py-2.5">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${u.aiProvider === 'gemini' ? 'bg-purple-100 text-purple-700' : 'bg-green-100 text-green-700'}`}>
                        {u.aiProvider}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-gray-700 font-medium">{u.rideCount}</td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">
                      {u.lastActivityDate ?? '—'}
                    </td>
                    <td className="px-3 py-2.5 text-gray-700">{u.chatMessageCount}</td>
                    <td className="px-3 py-2.5 text-gray-700 font-medium">{fmt(u.consumedTokens, language)}</td>
                    <td className="px-3 py-2.5">
                      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${u.stravaConnected ? 'bg-orange-100 text-orange-700' : 'bg-gray-100 text-gray-400'}`}>
                        {/* Glyphs the browser might not have, replaced with
                            icons (ai-trainer-ops#51.7). `aria-hidden`: the word
                            beside each one already says which state it is. */}
                        {u.stravaConnected ? (
                          u.stravaAnalysisComplete ? (
                            <span className="inline-flex items-center gap-1">
                              <Check size={11} aria-hidden="true" />
                              {t('synced')}
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1">
                              <Hourglass size={11} aria-hidden="true" />
                              {t('pending')}
                            </span>
                          )
                        ) : (
                          t('none')
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => void handleRevokeSessions(u)}
                          disabled={revokingUserId === u.id}
                          className="text-gray-300 hover:text-amber-600 transition-colors disabled:opacity-40"
                          /* Not "Sign out …": that is the accessible name of
                             the panel's own sign-out button in the header, and
                             two buttons answering to it would be ambiguous to
                             a screen reader and to a test. */
                          title={t('Revoke all sessions')}
                        >
                          <LogOut size={15} />
                        </button>
                        <button
                          onClick={() => openDeleteModal(u)}
                          className="text-gray-300 hover:text-red-500 transition-colors"
                          title={t('Delete user')}
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <AdminInviteCodes token={adminToken} />
        <AdminWrongFlags token={adminToken} />
      </div>

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <div className="flex items-center gap-2 mb-1">
              <Trash2 size={18} className="text-red-500" />
              <h2 className="text-base font-bold text-gray-900">{t('Delete account permanently')}</h2>
            </div>
            <p className="text-sm text-gray-500 mb-4">
              {slots(
                t("This will erase {name}'s account and all associated data (rides, chats, training plans, Strava connection). This cannot be undone."),
                {
                  name: (
                    <span key="name" className="font-semibold text-gray-800">
                      {deleteTarget.name ?? deleteTarget.email}
                    </span>
                  ),
                },
              )}
            </p>
            <p className="text-xs text-gray-500 mb-2">
              {/* The phrase itself stays as it is in every language: it is what the check compares. */}
              {slots(t('Type {phrase} to confirm:'), {
                phrase: (
                  <span key="phrase" className="font-mono bg-gray-100 px-1 py-0.5 rounded">
                    delete {deleteTarget.email}
                  </span>
                ),
              })}
            </p>
            <input
              type="text"
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-hidden focus:ring-2 focus:ring-red-500 mb-4"
              placeholder={`delete ${deleteTarget.email}`}
              autoFocus
              disabled={deleteLoading}
            />
            {deleteError && (
              <p className="text-xs text-red-600 mb-3">{t(deleteError)}</p>
            )}
            <div className="flex gap-2 justify-end">
              <button
                onClick={closeDeleteModal}
                disabled={deleteLoading}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 border border-gray-200 rounded-lg transition-colors disabled:opacity-50"
              >
                {t('Cancel')}
              </button>
              <button
                onClick={() => { void handleDelete() }}
                disabled={deleteConfirmText !== `delete ${deleteTarget.email}` || deleteLoading}
                className="px-4 py-2 text-sm font-semibold text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors disabled:opacity-40 flex items-center gap-2"
              >
                {deleteLoading && <Loader2 size={14} className="animate-spin" />}
                {t('Delete permanently')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
