import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAppStore } from './store/useAppStore'
import Layout from './components/Layout'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import AuthCallbackPage from './pages/AuthCallbackPage'
import OnboardingPage from './pages/OnboardingPage'
import DashboardPage from './pages/DashboardPage'
import WorkoutPage from './pages/WorkoutPage'
import StravaCallbackPage from './pages/StravaCallbackPage'
import SettingsPage from './pages/SettingsPage'
import AdminPage from './pages/AdminPage'
import { useImportProgress } from './hooks/useImportProgress'
import { AUTH_EXPIRED_EVENT } from './services/api'

const USER_DATA_LOADING_STEPS = 8

export default function App() {
  const authToken = useAppStore((s) => s.authToken)
  const isOnboarded = useAppStore((s) => s.isOnboarded)
  const isLoadingUserData = useAppStore((s) => s.isLoadingUserData)
  const loadingStep = useAppStore((s) => s.loadingStep)
  const loadUserData = useAppStore((s) => s.loadUserData)
  const logout = useAppStore((s) => s.logout)
  const dataLoadWarning = useAppStore((s) => s.dataLoadWarning)
  const clearDataLoadWarning = useAppStore((s) => s.clearDataLoadWarning)
  const importProgress = useImportProgress()

  // Whether the server has yet said *who this session is*. A stored token is
  // read synchronously at start-up, but `isOnboarded` is not persisted and
  // starts `false`, so on the first render of a full page load the app holds a
  // session it knows nothing about — and the route tree below used to answer
  // anyway (ai-trainer-ops#57).
  //
  // What that cost: loading `/settings` directly took the `!isOnboarded` branch,
  // whose only route is `/onboarding`, so `path="*"` replaced the URL; the
  // profile then arrived, the authenticated tree mounted, `/onboarding` was not
  // in it, and `path="*"` replaced the URL again — landing on the dashboard. Two
  // redirects, neither an authorisation decision, and the athlete's bookmark
  // thrown away by the first one. The loading screen did not hide it either: it
  // is an overlay drawn *beside* `<Routes>`, not instead of it, so all of this
  // happened behind "Loading your training data…".
  //
  // So: "not loaded yet" is its own state and the router is not asked until it
  // is over. It starts as "known" for a visitor with no token, because there is
  // nothing to find out about them.
  // Which token has been looked up, rather than a boolean for whether some
  // token has. Derived, so there is no render in which a *new* token looks
  // already-checked: a boolean reset inside the effect is still `true` for the
  // one render in which `authToken` changed from null to a value, which is
  // exactly a fresh sign-in — and that render would take the not-onboarded
  // branch and replace the URL before the effect could hold it. Found in review
  // on PR #798.
  const [checkedToken, setCheckedToken] = useState<string | null>(null)
  // Three conditions, and the third is not redundant. `loadUserData` dedupes
  // concurrent loads for one token and the skipped caller gets an immediate
  // `undefined` (#458) — and on the *real* sign-in path the login page starts
  // the load first, so App's call is always the deduped one. Its `.finally`
  // therefore fires at once and marks the token checked while the profile is
  // still in flight, with `isOnboarded` still false: the redirect this gate
  // exists to prevent, on every fresh sign-in. `!isLoadingUserData` is what
  // closes that, and it cannot stand alone either — on the first render of a
  // full page load no load has started yet, so only `checkedToken` knows.
  // Found in review on PR #798.
  const profileChecked =
    !authToken || (checkedToken === authToken && !isLoadingUserData)

  // A warning for whoever adds the first mid-session reload: because
  // `isLoadingUserData` is part of the condition, the gate *re-holds* every time
  // a load starts again, and a re-hold unmounts the authenticated tree. Today
  // that is harmless — the only callers of `loadUserData` are this effect and
  // the three sign-in pages, so it never runs except at sign-in. A "refresh my
  // data" button calling it would blank whatever page the athlete was on.
  // If that day comes, ai-trainer-ops#58 is the fix: once a deduped call
  // resolves with the real load, `checkedToken` alone is enough and this term
  // can go.

  useEffect(() => {
    if (!authToken) {
      // Not strictly needed — `!authToken` already reports "checked" — but a
      // stale token left here would re-open the original hole if a sign-out and
      // sign-in ever produced the same token string.
      setCheckedToken(null)
      return
    }
    let cancelled = false
    // `.finally` rather than `.then`, though the two are equivalent today:
    // `loadUserData` catches everything and returns, so it does not reject, and
    // no test can tell the two apart — the review on PR #798 asked and the
    // answer is that it cannot. Kept because the thing that must hold is "an
    // attempt has been made", not "an attempt succeeded": the day that function
    // grows a path that throws, `.then` would hold the athlete on a blank page
    // for as long as the backend stayed unreachable, and nothing would point
    // here.
    void loadUserData().finally(() => {
      if (!cancelled) setCheckedToken(authToken)
    })
    return () => {
      cancelled = true
    }
  }, [authToken, loadUserData])

  // A 401 on a request that carried a token means the session is finished —
  // expired, or revoked by "sign out everywhere" or an operator (#704).
  //
  // This used to also kick off a `GET /auth/session` probe against Authelia,
  // on every logged-out page load, behind an overlay reading "Checking your
  // secure session… Authelia will continue sign-in if needed." It could never
  // succeed here: that endpoint answers from `Remote-*` headers, and
  // forward-auth is attached to no router (#696). So every visitor paid a round
  // trip and a flash of Authelia before the landing page, and every one of them
  // wrote `WARNING: Authelia session not found` to the backend log.
  //
  // `/auth/callback` still calls it, deliberately — that route exists for a
  // portal-authenticated user to be sent to, and costs nothing while nobody is.
  // Reinstate the automatic probe when forward-auth is actually wired up, not
  // before; see `endSession` for the rest of that dormant design.
  useEffect(() => {
    const handleAuthExpired = () => logout()
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  }, [logout])

  const showOverlay = Boolean(authToken && isLoadingUserData)
  const isPreparingImport = importProgress.status === 'running' && importProgress.total === 0
  const hasGlobalImportProgress = importProgress.status === 'running' && importProgress.total > 0
  const showPercent = !isPreparingImport
  const pct = hasGlobalImportProgress
    ? Math.round((Math.min(importProgress.processed, importProgress.total) / importProgress.total) * 100)
    : Math.round((Math.min(loadingStep, USER_DATA_LOADING_STEPS) / USER_DATA_LOADING_STEPS) * 100)
  const loadingTitle = 'Loading your training data...'
  let loadingSubtitle = 'Syncing profile, plan, workouts, and chat.'
  if (isPreparingImport) {
    loadingSubtitle = 'Preparing Strava import...'
  } else if (hasGlobalImportProgress) {
    loadingSubtitle = `Processing activities: ${Math.min(importProgress.processed, importProgress.total)} / ${importProgress.total}`
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/strava/callback" element={<StravaCallbackPage />} />
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route path="/admin" element={<AdminPage />} />

        {!profileChecked ? (
          // Deliberately no element and, above all, no `<Navigate>`: the URL has
          // to survive until there is something to decide with. The overlay below
          // is what the athlete sees meanwhile.
          //
          // No `authToken &&` guard: `profileChecked` is derived from the token
          // itself, so a signed-out visitor is "checked" by definition and
          // cannot land here — not even for the one render after a logout that
          // interrupted a load, which an `isLoading`-style boolean would have
          // spent on a blank page.
          <Route path="*" element={null} />
        ) : !authToken ? (
          <>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </>
        ) : !isOnboarded ? (
          <>
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route path="*" element={<Navigate to="/onboarding" replace />} />
          </>
        ) : (
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/workout/:date" element={<WorkoutPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        )}
      </Routes>
      {dataLoadWarning && !isLoadingUserData && (
        <div className="fixed top-0 inset-x-0 z-50 flex items-center justify-between gap-4 bg-amber-50 border-b border-amber-200 px-4 py-2 text-sm text-amber-800">
          <span className="flex items-center gap-1.5">
            <AlertTriangle size={14} className="shrink-0" aria-hidden="true" />
            {dataLoadWarning}
          </span>
          <button
            onClick={clearDataLoadWarning}
            className="shrink-0 text-amber-600 hover:text-amber-900 font-medium"
          >
            Dismiss
          </button>
        </div>
      )}
      {showOverlay && (
        <div className="fixed inset-0 bg-[#0f1116] flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl shadow-2xl px-8 py-6 text-center w-72">
            <p className="text-sm font-semibold text-gray-900">{loadingTitle}</p>
            <p className="text-xs text-gray-500 mt-1">{loadingSubtitle}</p>
            <div className="mt-4 w-full bg-gray-200 rounded-full h-2 overflow-hidden">
              {showPercent ? (
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all duration-300 ease-out"
                  style={{ width: `${pct}%` }}
                />
              ) : (
                <div className="h-2 w-full bg-blue-100 relative overflow-hidden">
                  <div className="absolute inset-y-0 -left-1/2 w-1/2 bg-blue-600 rounded-full animate-[pulse_1.2s_ease-in-out_infinite]" />
                </div>
              )}
            </div>
            <p className="text-xs text-gray-400 mt-1">{showPercent ? `${pct}%` : 'Starting import...'}</p>
          </div>
        </div>
      )}
    </BrowserRouter>
  )
}
