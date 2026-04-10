import { useState } from 'react'
import { Eye, EyeOff, Save, Trash2, AlertTriangle, Server } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import StravaConnect from '../components/StravaConnect'
import type { AiProvider } from '../services/ai'

const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL as string | undefined ?? 'http://localhost:8000').replace(/\/$/, '')

export default function SettingsPage() {
  const {
    aiProvider,
    aiApiKey,
    stravaTokens,
    setAiProvider,
    setAiApiKey,
    resetAll,
  } = useAppStore(
    useShallow((s) => ({
      aiProvider: s.aiProvider,
      aiApiKey: s.aiApiKey,
      stravaTokens: s.stravaTokens,
      setAiProvider: s.setAiProvider,
      setAiApiKey: s.setAiApiKey,
      resetAll: s.resetAll,
    }))
  )

  const [selectedProvider, setSelectedProvider] = useState<AiProvider>(aiProvider)
  const [apiKey, setApiKey] = useState(aiApiKey)
  const [showKey, setShowKey] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')

  const saveAI = () => {
    setAiProvider(selectedProvider)
    setAiApiKey(apiKey)
    setSavedMsg('AI settings saved!')
    setTimeout(() => setSavedMsg(''), 2000)
  }

  const handleReset = () => {
    if (window.confirm('Are you sure? This will delete all your data and training plan.')) {
      resetAll()
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
        <p className="text-sm text-gray-500 mt-0.5">Manage your AI provider, API keys, and integrations</p>
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
          Choose which AI service powers your training plan and coach chat.
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

        <label className="block text-sm font-medium text-gray-700 mb-1">
          {providers.find((p) => p.value === selectedProvider)?.label} API Key
        </label>
        <div className="relative mb-3">
          <input
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-amber-500 focus:border-amber-500"
            placeholder={providers.find((p) => p.value === selectedProvider)?.placeholder}
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
            Your API key is stored only in your browser's localStorage and is sent directly to{' '}
            {selectedProvider === 'openai' ? 'OpenAI' : 'Google'}.
            Never share your API key with anyone.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            onClick={saveAI}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600"
          >
            <Save size={15} /> Save
          </button>
          <button
            onClick={() => { setApiKey(''); setAiApiKey('') }}
            className="flex items-center gap-1.5 border border-gray-300 text-gray-600 rounded-lg px-4 py-2 text-sm hover:bg-gray-50"
          >
            <Trash2 size={15} /> Clear Key
          </button>
        </div>
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

        {stravaTokens ? (
          <StravaConnect />
        ) : (
          <StravaConnect />
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
