import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAppStore } from './store/useAppStore'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import OnboardingPage from './pages/OnboardingPage'
import DashboardPage from './pages/DashboardPage'
import ExpertPage from './pages/ExpertPage'
import WorkoutPage from './pages/WorkoutPage'
import StravaCallbackPage from './pages/StravaCallbackPage'
import SettingsPage from './pages/SettingsPage'
import { useImportProgress } from './hooks/useImportProgress'

const USER_DATA_LOADING_STEPS = 8

export default function App() {
  const authToken = useAppStore((s) => s.authToken)
  const isOnboarded = useAppStore((s) => s.isOnboarded)
  const isLoadingUserData = useAppStore((s) => s.isLoadingUserData)
  const loadingStep = useAppStore((s) => s.loadingStep)
  const loadUserData = useAppStore((s) => s.loadUserData)
  const importProgress = useImportProgress()

  useEffect(() => {
    if (authToken) {
      void loadUserData().catch(() => {})
    }
  }, [authToken, loadUserData])

  const showOverlay = Boolean(authToken && isLoadingUserData)
  const isPreparingImport = importProgress.status === 'running' && importProgress.total === 0
  const hasGlobalImportProgress = importProgress.status === 'running' && importProgress.total > 0
  const showPercent = !isPreparingImport
  const pct = hasGlobalImportProgress
    ? Math.round((Math.min(importProgress.processed, importProgress.total) / importProgress.total) * 100)
    : Math.round((Math.min(loadingStep, USER_DATA_LOADING_STEPS) / USER_DATA_LOADING_STEPS) * 100)
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

        {!authToken ? (
          <>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="*" element={<Navigate to="/login" replace />} />
          </>
        ) : !isOnboarded ? (
          <>
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route path="*" element={<Navigate to="/onboarding" replace />} />
          </>
        ) : (
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/expert" element={<ExpertPage />} />
            <Route path="/workout/:date" element={<WorkoutPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        )}
      </Routes>
      {showOverlay && (
        <div className="fixed inset-0 bg-gradient-to-br from-[#1a1a2e] to-[#16213e] flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl shadow-2xl px-8 py-6 text-center w-72">
            <p className="text-sm font-semibold text-gray-900">Loading your training data...</p>
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
