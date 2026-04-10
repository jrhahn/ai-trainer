import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAppStore } from './store/useAppStore'
import Layout from './components/Layout'
import OnboardingPage from './pages/OnboardingPage'
import DashboardPage from './pages/DashboardPage'
import WorkoutPage from './pages/WorkoutPage'
import StravaCallbackPage from './pages/StravaCallbackPage'
import SettingsPage from './pages/SettingsPage'

export default function App() {
  const isOnboarded = useAppStore((s) => s.isOnboarded)

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/strava/callback" element={<StravaCallbackPage />} />
        {!isOnboarded ? (
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
