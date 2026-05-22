import { useState } from 'react'
import { Shield, Loader2, LogOut, Users, Zap, Activity } from 'lucide-react'
import { apiFetch, API_BASE } from '../services/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AdminUserStat {
  id: string
  email: string
  name: string | null
  createdAt: string
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

function fmt(n: number): string {
  return n.toLocaleString()
}

function relativeDate(iso: string): string {
  const d = new Date(iso)
  const diff = Date.now() - d.getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days}d ago`
  if (days < 365) return `${Math.floor(days / 30)}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({ label, value, icon }: { label: string; value: string | number; icon: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-5 py-4 shadow-sm flex items-center gap-4">
      <div className="text-blue-500">{icon}</div>
      <div>
        <p className="text-xs text-gray-400 uppercase tracking-wider font-semibold">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{typeof value === 'number' ? fmt(value) : value}</p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const [password, setPassword] = useState('')
  const [adminToken, setAdminToken] = useState<string | null>(null)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginLoading, setLoginLoading] = useState(false)

  const [stats, setStats] = useState<AdminUsersResponse | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [statsError, setStatsError] = useState<string | null>(null)

  const [sortKey, setSortKey] = useState<keyof AdminUserStat>('createdAt')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  // -------------------------------------------------------------------------
  // Login
  // -------------------------------------------------------------------------

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginError(null)
    setLoginLoading(true)
    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({})) as { detail?: string }
        throw new Error(data.detail ?? 'Login failed')
      }
      const data = await res.json() as { access_token: string }
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
      <div className="min-h-screen bg-gradient-to-br from-[#1a1a2e] to-[#16213e] flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-8">
          <div className="flex items-center gap-2 mb-6">
            <div className="bg-blue-600 rounded-lg p-1.5">
              <Shield size={22} className="text-white" />
            </div>
            <span className="font-bold text-xl text-gray-900">Admin Panel</span>
          </div>

          <form onSubmit={(e) => { void handleLogin(e) }} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Admin password"
                required
                autoFocus
              />
            </div>
            {loginError && (
              <p className="text-sm text-red-600">{loginError}</p>
            )}
            <button
              type="submit"
              disabled={loginLoading}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-lg text-sm transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
            >
              {loginLoading && <Loader2 size={16} className="animate-spin" />}
              Sign In
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
            <h1 className="text-xl font-bold text-gray-900">Admin Panel</h1>
          </div>
          <button
            onClick={() => { setAdminToken(null); setStats(null) }}
            className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <LogOut size={15} />
            Sign out
          </button>
        </div>

        {/* Summary cards */}
        {stats && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
            <StatCard label="Total users" value={stats.totalUsers} icon={<Users size={22} />} />
            <StatCard label="Total tokens" value={stats.totalTokens} icon={<Zap size={22} />} />
            <StatCard
              label="Strava connected"
              value={stats.users.filter((u) => u.stravaConnected).length}
              icon={<Activity size={22} />}
            />
          </div>
        )}

        {/* Error / loading */}
        {statsError && (
          <p className="text-sm text-red-600 mb-4">{statsError}</p>
        )}
        {statsLoading && (
          <div className="flex items-center gap-2 text-gray-500 text-sm mb-4">
            <Loader2 size={16} className="animate-spin" /> Loading…
          </div>
        )}

        {/* Table */}
        {stats && (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <SortTh label="Name / Email" field="email" />
                  <SortTh label="Joined" field="createdAt" />
                  <SortTh label="Provider" field="aiProvider" />
                  <SortTh label="Rides" field="rideCount" />
                  <SortTh label="Last ride" field="lastActivityDate" />
                  <SortTh label="Chats" field="chatMessageCount" />
                  <SortTh label="Tokens" field="consumedTokens" />
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">Strava</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sortedUsers.map((u) => (
                  <tr key={u.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-3 py-2.5">
                      <p className="font-medium text-gray-900 truncate max-w-[180px]">{u.name ?? '—'}</p>
                      <p className="text-xs text-gray-400 truncate max-w-[180px]">{u.email}</p>
                    </td>
                    <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap">{relativeDate(u.createdAt)}</td>
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
                    <td className="px-3 py-2.5 text-gray-700 font-medium">{fmt(u.consumedTokens)}</td>
                    <td className="px-3 py-2.5">
                      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${u.stravaConnected ? 'bg-orange-100 text-orange-700' : 'bg-gray-100 text-gray-400'}`}>
                        {u.stravaConnected ? (u.stravaAnalysisComplete ? '✓ synced' : '⧗ pending') : 'none'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
