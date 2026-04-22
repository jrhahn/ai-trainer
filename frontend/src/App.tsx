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

export default function App() {
  const authToken = useAppStore((s) => s.authToken)
  const isOnboarded = useAppStore((s) => s.isOnboarded)
  const isLoadingUserData = useAppStore((s) => s.isLoadingUserData)
  const loadingStep = useAppStore((s) => s.loadingStep)
  const loadUserData = useAppStore((s) => s.loadUserData)

  useEffect(() => {
    if (authToken) {
      void loadUserData().catch(() => {})
    }
  }, [authToken, loadUserData])

  if (authToken && isLoadingUserData) {
    const pct = Math.round((loadingStep / 6) * 100)
    return (
      <div className="min-h-screen bg-gradient-to-br from-[#1a1a2e] to-[#16213e] flex items-center justify-center">
        <div className="bg-white rounded-2xl shadow-2xl px-8 py-6 text-center w-72">
          <p className="text-sm font-semibold text-gray-900">Loading your training data...</p>
          <p className="text-xs text-gray-500 mt-1">Syncing profile, plan, workouts, and chat.</p>
          <div className="mt-4 w-full bg-gray-200 rounded-full h-2 overflow-hidden">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all duration-300 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-xs text-gray-400 mt-1">{pct}%</p>
        </div>
      </div>
    )
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
    </BrowserRouter>
  )
}
