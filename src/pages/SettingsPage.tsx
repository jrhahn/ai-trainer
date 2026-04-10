import { useState } from 'react'
import { Eye, EyeOff, Save, Trash2, AlertTriangle } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import StravaConnect from '../components/StravaConnect'
import { getStravaAuthUrl } from '../services/strava'

export default function SettingsPage() {
  const {
    openaiApiKey,
    stravaClientId,
    stravaClientSecret,
    stravaTokens,
    setOpenaiApiKey,
    setStravaConfig,
    resetAll,
  } = useAppStore(
    useShallow((s) => ({
      openaiApiKey: s.openaiApiKey,
      stravaClientId: s.stravaClientId,
      stravaClientSecret: s.stravaClientSecret,
      stravaTokens: s.stravaTokens,
      setOpenaiApiKey: s.setOpenaiApiKey,
      setStravaConfig: s.setStravaConfig,
      resetAll: s.resetAll,
    }))
  )

  const [apiKey, setApiKey] = useState(openaiApiKey)
  const [showKey, setShowKey] = useState(false)
  const [clientId, setClientId] = useState(stravaClientId)
  const [clientSecret, setClientSecret] = useState(stravaClientSecret)
  const [showSecret, setShowSecret] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')

  const saveOpenAI = () => {
    setOpenaiApiKey(apiKey)
    setSavedMsg('OpenAI key saved!')
    setTimeout(() => setSavedMsg(''), 2000)
  }

  const saveStrava = () => {
    setStravaConfig(clientId, clientSecret)
    setSavedMsg('Strava config saved!')
    setTimeout(() => setSavedMsg(''), 2000)
  }

  const handleStravaConnect = () => {
    if (!clientId) {
      alert('Enter your Strava Client ID first and save.')
      return
    }
    setStravaConfig(clientId, clientSecret)
    const redirectUri = window.location.origin + '/strava/callback'
    window.location.href = getStravaAuthUrl(clientId, redirectUri)
  }

  const handleReset = () => {
    if (window.confirm('Are you sure? This will delete all your data and training plan.')) {
      resetAll()
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">Manage your API keys and integrations</p>
      </div>

      {savedMsg && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-xl px-4 py-3 text-sm">
          {savedMsg}
        </div>
      )}

      {/* OpenAI */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">OpenAI API Key</h2>
        <p className="text-xs text-gray-500 mb-4">
          Used to generate and adapt your training plan with AI.
        </p>
        <div className="relative mb-3">
          <input
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-amber-500 focus:border-amber-500"
            placeholder="sk-..."
          />
          <button
            type="button"
            onClick={() => setShowKey(!showKey)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4 flex gap-2">
          <AlertTriangle size={16} className="text-yellow-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-yellow-700">
            Your API key is stored only in your browser's localStorage and is sent directly to OpenAI.
            Never share your API key with anyone.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={saveOpenAI}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600"
          >
            <Save size={15} /> Save
          </button>
          <button
            onClick={() => { setApiKey(''); setOpenaiApiKey('') }}
            className="flex items-center gap-1.5 border border-gray-300 text-gray-600 rounded-lg px-4 py-2 text-sm hover:bg-gray-50"
          >
            <Trash2 size={15} /> Clear
          </button>
        </div>
      </div>

      {/* Strava */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h2 className="text-base font-bold text-gray-900 mb-1">Strava Integration</h2>
        <p className="text-xs text-gray-500 mb-4">
          Connect Strava to sync your activities automatically.
        </p>

        <div className="space-y-3 mb-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="Your Strava App Client ID"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Client Secret</label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-amber-500 focus:border-amber-500"
                placeholder="Your Strava App Client Secret"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                {showSecret ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>
        </div>

        <div className="flex gap-2 mb-4">
          <button
            onClick={saveStrava}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600"
          >
            <Save size={15} /> Save Config
          </button>
        </div>

        {stravaTokens ? (
          <StravaConnect />
        ) : (
          <button
            onClick={handleStravaConnect}
            className="flex items-center gap-2 bg-[#fc4c02] text-white rounded-xl px-5 py-3 font-semibold text-sm hover:bg-[#e03d00] transition-colors"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
              <path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169" />
            </svg>
            Connect to Strava
          </button>
        )}
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
