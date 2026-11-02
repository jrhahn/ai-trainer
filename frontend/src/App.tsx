import { lazy, Suspense, useEffect, useState } from 'react'
import { useLanguage, useT } from './i18n'
import { AlertTriangle } from 'lucide-react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { USER_DATA_LOADS, useAppStore } from './store/useAppStore'
import { setInterfaceLanguage } from './services/api'
import Layout from './components/Layout'
import LandingPage from './pages/LandingPage'
import PageLoadBoundary from './components/PageLoadBoundary'
import ProfileLoadFailed from './components/ProfileLoadFailed'
import DashboardPage from './pages/DashboardPage'
import { useImportProgress } from './hooks/useImportProgress'
import { AUTH_EXPIRED_EVENT } from './services/api'
import { onSignedOutElsewhere, resumeSession, type Resumed } from './services/auth'
import { UNREACHABLE_MESSAGE } from './services/api'
import { button, card, cardTitle } from './components/ui'

// Every page but the first screen, loaded when it is opened (ai-trainer-ops#275):
// everything was one 977 kB script, read on a phone before the first screen.
// The first screen is the landing page or the dashboard, so those stay.
const LoginPage = lazy(() => import('./pages/LoginPage'))
const RegisterPage = lazy(() => import('./pages/RegisterPage'))
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'))
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage'))
const VerifyEmailPage = lazy(() => import('./pages/VerifyEmailPage'))
const AuthCallbackPage = lazy(() => import('./pages/AuthCallbackPage'))
const StravaCallbackPage = lazy(() => import('./pages/StravaCallbackPage'))
const OnboardingPage = lazy(() => import('./pages/OnboardingPage'))
const WorkoutPage = lazy(() => import('./pages/WorkoutPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))
const SetupGuidePage = lazy(() => import('./pages/SetupGuidePage'))
const PowerCurvePage = lazy(() => import('./pages/PowerCurvePage'))
const PrivacyPage = lazy(() => import('./pages/PrivacyPage'))
const LegalNoticePage = lazy(() => import('./pages/LegalNoticePage'))


export default function App() {
  const t = useT()
  const authToken = useAppStore((s) => s.authToken)
  const isOnboarded = useAppStore((s) => s.isOnboarded)
  const profileLoadFailed = useAppStore((s) => s.profileLoadFailed)
  const isLoadingUserData = useAppStore((s) => s.isLoadingUserData)
  const loadingStep = useAppStore((s) => s.loadingStep)
  const loadUserData = useAppStore((s) => s.loadUserData)
  const logout = useAppStore((s) => s.logout)
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const dataLoadWarning = useAppStore((s) => s.dataLoadWarning)
  const dataLoadFailures = useAppStore((s) => s.dataLoadFailures)
  const notice = useAppStore((s) => s.notice)
  const clearDataLoadWarning = useAppStore((s) => s.clearDataLoadWarning)
  const importProgress = useImportProgress()
  // The page's language follows the interface's, for screen readers and the
  // browser's own translation offer (ai-trainer-ops#103 Q27).
  const language = useLanguage()
  useEffect(() => setInterfaceLanguage(language), [language])
  useEffect(() => {
    document.documentElement.lang = language
  }, [language])

  // Whether the server has said if this browser holds a session at all
  // (ai-trainer-ops#45). The session is an HttpOnly cookie, so the client cannot
  // look; until the answer arrives nothing is rendered, not even the callback
  // routes, which would otherwise start with no session and report it expired.
  const [resumed, setResumed] = useState<Resumed['state'] | null>(null)
  // Raised by "Try again" when the server did not answer the session check.
  const [resumeAttempt, setResumeAttempt] = useState(0)
  useEffect(() => {
    let cancelled = false
    void resumeSession().then((result) => {
      if (cancelled) return
      // A sign-in that finished first owns the session.
      if (result.state === 'session' && !useAppStore.getState().authToken) setAuthToken(result.marker)
      setResumed(result.state)
    })
    return () => {
      cancelled = true
    }
  }, [setAuthToken, resumeAttempt])

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
  // On the *real* sign-in path the login page starts the load first, so App's
  // call is always the deduped one (#458). That is safe only because a deduped
  // call resolves with the load it joined (ai-trainer-ops#58): when it returned
  // at once, its `.finally` marked the token checked while the profile was still
  // in flight, with `isOnboarded` still false — the redirect this gate exists to
  // prevent, on every fresh sign-in (found in review on PR #798). The gate is
  // deliberately not also tied to `isLoadingUserData`: that would re-hold it,
  // and unmount the authenticated tree, every time a later reload started.
  const profileChecked = !authToken || checkedToken === authToken
  // Not strictly needed — `!authToken` already reports "checked" — but a stale
  // token left here would re-open the original hole if a sign-out and sign-in
  // ever produced the same token string. Reset while rendering, React's way for
  // state that follows a value, not in the effect (ai-trainer-ops#286).
  if (!authToken && checkedToken !== null) setCheckedToken(null)

  useEffect(() => {
    if (!authToken) return
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

  // A sign-out in another tab of this browser signs this one out too (ops#130).
  useEffect(() => onSignedOutElsewhere(logout), [logout])

  const showOverlay = Boolean(authToken && isLoadingUserData)
  const isPreparingImport = importProgress.status === 'running' && importProgress.total === 0
  const hasGlobalImportProgress = importProgress.status === 'running' && importProgress.total > 0
  const showPercent = !isPreparingImport
  const pct = hasGlobalImportProgress
    ? Math.round((Math.min(importProgress.processed, importProgress.total) / importProgress.total) * 100)
    : Math.round((Math.min(loadingStep, USER_DATA_LOADS) / USER_DATA_LOADS) * 100)
  const loadingTitle = t('Loading your training data…')
  let loadingSubtitle = t('Syncing profile, plan, workouts, and chat.')
  if (isPreparingImport) {
    loadingSubtitle = t('Preparing the activity import…')
  } else if (hasGlobalImportProgress) {
    loadingSubtitle = t('Processing activities: {done} / {total}', {
      done: Math.min(importProgress.processed, importProgress.total),
      total: importProgress.total,
    })
  }

  // A failed load and a failed action, side by side (ai-trainer-ops#272). The
  // names translate; a warning set without them stays as written (ai-trainer-ops#121).
  const loadWarning =
    dataLoadFailures.length > 0
      ? t('Some data failed to load ({items}).', {
          items: dataLoadFailures.map((item) => t(item)).join(', '),
        })
      : dataLoadWarning
  const warning = [loadWarning, notice].filter(Boolean).join(' ')

  if (resumed === null) return null
  // No answer from the server is not "signed out": the landing page said so to
  // an athlete on a dead connection (ai-trainer-ops#267).
  if (resumed === 'unreachable' && !authToken) {
    return (
      <ServerUnreachable
        onRetry={() => {
          setResumed(null)
          setResumeAttempt((attempt) => attempt + 1)
        }}
      />
    )
  }

  return (
    <BrowserRouter>
      {/* Nothing while a page's code arrives: the same as before the session check. */}
      <PageLoadBoundary>
      <Suspense fallback={null}>
      <Routes>
        <Route path="/strava/callback" element={<StravaCallbackPage />} />
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/curve" element={<PowerCurvePage />} />
        {/* Signed in or not: the sign-up form links here (ai-trainer-ops#2). */}
        <Route path="/privacy" element={<PrivacyPage />} />
        {/* Signed in or not: § 5 DDG wants it reachable from every page (ai-trainer-ops#1). */}
        <Route path="/impressum" element={<LegalNoticePage />} />
        {/* Signed in or not: the landing page and the invitation link here (ai-trainer-ops#305). */}
        <Route path="/anleitung" element={<SetupGuidePage language="de" />} />
        <Route path="/guide" element={<SetupGuidePage language="en" />} />
        {/* Reachable signed in or out: a reset link opens wherever it is clicked. */}
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />

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
        ) : profileLoadFailed ? (
          // Unknown is not "not onboarded" (ai-trainer-ops#118): onboarding
          // would save its defaults over the race and the plan. No redirect, so
          // the retry lands on the URL the athlete asked for.
          <Route
            path="*"
            element={<ProfileLoadFailed onRetry={() => void loadUserData()} onSignOut={logout} />}
          />
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
      </Suspense>
      </PageLoadBoundary>
      {warning && !isLoadingUserData && (
        // Below the 56 px mobile top bar (Layout), so its menu button stays
        // reachable; at the top on wider screens, which have no such bar
        // (ai-trainer-ops#131).
        // Beside the 14rem sidebar from `md` up, not over its logo; and a retry
        // in the banner, since an app on the home screen has no reload button
        // (review of 2026-11-01, ai-trainer-ops#338 A13).
        <div className="fixed top-14 md:top-0 inset-x-0 md:left-56 z-50 flex items-center justify-between gap-4 bg-amber-50 border-b border-amber-200 px-4 py-2 text-sm text-amber-800">
          <span className="flex items-center gap-1.5">
            <AlertTriangle size={14} className="shrink-0" aria-hidden="true" />
            {warning}
          </span>
          {dataLoadFailures.length > 0 && (
            <button
              onClick={() => void loadUserData()}
              className="shrink-0 min-h-10 px-2 text-amber-700 hover:text-amber-900 font-semibold"
            >
              {t('Try again')}
            </button>
          )}
          <button
            onClick={clearDataLoadWarning}
            className="shrink-0 min-h-10 px-2 text-amber-600 hover:text-amber-900 font-medium"
          >
            {t('Dismiss')}
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
            <p className="text-xs text-gray-400 mt-1">{showPercent ? `${pct}%` : t('Starting import …')}</p>
          </div>
        </div>
      )}
    </BrowserRouter>
  )
}

/** The session check got no answer: say so, and offer to ask again. */
function ServerUnreachable({ onRetry }: { onRetry: () => void }) {
  const t = useT()
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-100 p-4">
      <div className={`${card} max-w-sm space-y-3 text-center`}>
        <h1 className={cardTitle}>{t('No connection to the server')}</h1>
        <p className="text-sm text-gray-500">{t(UNREACHABLE_MESSAGE)}</p>
        <button type="button" onClick={onRetry} className={button}>
          {t('Try again')}
        </button>
      </div>
    </div>
  )
}

