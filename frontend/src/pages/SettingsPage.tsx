import { useState } from 'react'
import { Save, Trash2, AlertTriangle, Server, LogOut } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import StravaConnect from '../components/StravaConnect'
import type { AiProvider } from '../store/useAppStore'
import { BACKEND_URL, AUTHELIA_URL } from '../services/api'
import { deleteCurrentUser, updateCurrentUser } from '../services/user'

export default function SettingsPage() {
  const {
    authToken,
    userProfile,
    aiProvider,
    setAiProvider,
    resetAll,
    logout,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      aiProvider: s.aiProvider,
      setAiProvider: s.setAiProvider,
      resetAll: s.resetAll,
      logout: s.logout,
    }))
  )

  const [selectedProvider, setSelectedProvider] = useState<AiProvider>(aiProvider)
  const [savedMsg, setSavedMsg] = useState('')

  const saveAI = async () => {
    if (!authToken) return

    await updateCurrentUser(authToken, { aiProvider: selectedProvider })
    setAiProvider(selectedProvider)
    setSavedMsg('AI settings saved!')
    setTimeout(() => setSavedMsg(''), 2000)
  }

  const handleReset = async () => {
    if (!authToken) return
    if (window.confirm('Are you sure? This will delete all your data and training plan.')) {
      await deleteCurrentUser(authToken)
      resetAll()
    }
  }

  const handleLogout = () => {
    logout()
    if (AUTHELIA_URL) {
      window.location.href = `${AUTHELIA_URL}/logout`
    }
  }

  const providers: { value: AiProvider; label: string; hint: string; placeholder: string }[] = [
    { value: 'openai', label: 'OpenAI', hint: 'GPT-4o mini', placeholder: 'sk-...' },
    { value: 'gemini', label: 'Google Gemini', hint: 'Gemini 2.0 Flash', placeholder: 'AIza...' },
  ]

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">Manage your AI provider, account, and integrations</p>
      </div>

      {savedMsg && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-xl px-4 py-3 text-sm">
          {savedMsg}
        </div>
      )}

      {/* AI Provider */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">AI Provider</h2>
        <p className="text-xs text-gray-500 mb-4">
          Choose which server-side model powers your training plan and coach chat.
        </p>

        <div className="grid grid-cols-2 gap-3 mb-4">
          {providers.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setSelectedProvider(p.value)}
              className={`flex flex-col items-start px-4 py-3 rounded-xl border-2 text-left transition-all ${
                selectedProvider === p.value
                  ? 'border-amber-500 bg-amber-50'
                  : 'border-gray-200 hover:border-amber-300'
              }`}
            >
              <span className="font-semibold text-sm text-gray-900">{p.label}</span>
              <span className="text-xs text-gray-500">{p.hint}</span>
            </button>
          ))}
        </div>

        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4 flex gap-2">
          <AlertTriangle size={16} className="text-yellow-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-yellow-700">
            Model keys live on the backend now. The browser no longer stores or sends provider API keys.
          </p>
        </div>

        <div className="flex gap-2 flex-wrap">
          <button
            onClick={saveAI}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600"
          >
            <Save size={15} /> Save
          </button>
        </div>
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">Account</h2>
        <p className="text-xs text-gray-500 mb-4">Signed in as {userProfile?.email ?? 'unknown'}.</p>
        <button
          onClick={handleLogout}
          className="flex items-center gap-1.5 border border-gray-300 text-gray-700 rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-50"
        >
          <LogOut size={15} /> Sign Out
        </button>
      </div>

      {/* Strava */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">Strava Integration</h2>
        <p className="text-xs text-gray-500 mb-4">
          Connect Strava to sync your activities automatically.
        </p>

        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 flex gap-2">
          <Server size={16} className="text-blue-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-blue-700">
            OAuth is handled by the backend at <code className="font-mono bg-blue-100 px-1 rounded">{BACKEND_URL}</code>.
            Your Strava credentials are never stored in the browser.
          </p>
        </div>

        <StravaConnect />
      </div>

      {/* Danger zone */}
      <div className="bg-white rounded-2xl shadow-sm border border-red-100 p-6">
        <h2 className="text-base font-bold text-red-600 mb-1">Danger Zone</h2>
        <p className="text-xs text-gray-500 mb-4">
          Permanently delete all your data and start over from scratch.
        </p>
        <button
          onClick={handleReset}
          className="flex items-center gap-1.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-red-100"
        >
          <Trash2 size={15} /> Reset All Data
        </button>
      </div>
    </div>
  )
}
