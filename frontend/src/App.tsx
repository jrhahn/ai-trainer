import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAppStore } from './store/useAppStore'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import OnboardingPage from './pages/OnboardingPage'
import DashboardPage from './pages/DashboardPage'
import WorkoutPage from './pages/WorkoutPage'
import StravaCallbackPage from './pages/StravaCallbackPage'
import SettingsPage from './pages/SettingsPage'

export default function App() {
  const authToken = useAppStore((s) => s.authToken)
  const isOnboarded = useAppStore((s) => s.isOnboarded)
  const isLoadingUserData = useAppStore((s) => s.isLoadingUserData)
  const loadUserData = useAppStore((s) => s.loadUserData)

  useEffect(() => {
    if (authToken) {
      void loadUserData().catch(() => {})
    }
  }, [authToken, loadUserData])

  if (authToken && isLoadingUserData) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-[#1a1a2e] to-[#16213e] flex items-center justify-center">
        <div className="bg-white rounded-2xl shadow-2xl px-8 py-6 text-center">
          <p className="text-sm font-semibold text-gray-900">Loading your training data...</p>
          <p className="text-xs text-gray-500 mt-1">Syncing profile, plan, workouts, and chat.</p>
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
            <Route path="/workout/:date" element={<WorkoutPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        )}
      </Routes>
    </BrowserRouter>
  )
}
