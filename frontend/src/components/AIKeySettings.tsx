import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Key, Trash2, CheckCircle, AlertCircle, Loader } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import SetupGuideLink from './SetupGuideLink'
import { SETUP_GUIDE_SECTIONS } from '../utils/links'
import {
  fetchAIKeyStatus,
  saveAIKey,
  deleteAIKey,
  testAIKey,
  type AIKeyStatus,
} from '../services/user'

const AI_KEY_QUERY_KEY = 'ai-key-status'

const PROVIDERS = [
  { value: 'openai', label: 'OpenAI', placeholder: 'sk-...', hint: 'GPT-4o' },
  { value: 'gemini', label: 'Google Gemini', placeholder: 'AIza...', hint: 'Gemini 2.5 Flash' },
] as const

type Provider = (typeof PROVIDERS)[number]['value']

export default function AIKeySettings() {
  const { authToken } = useAppStore(useShallow((s) => ({ authToken: s.authToken })))
  const queryClient = useQueryClient()

  const [selectedProvider, setSelectedProvider] = useState<Provider>('openai')
  const [keyInput, setKeyInput] = useState('')
  const [testMsg, setTestMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [isTesting, setIsTesting] = useState(false)

  const { data: keyStatus } = useQuery<AIKeyStatus>({
    queryKey: [AI_KEY_QUERY_KEY],
    queryFn: () => fetchAIKeyStatus(authToken!),
    enabled: !!authToken,
  })

  const saveMutation = useMutation({
    mutationFn: ({ provider, key }: { provider: string; key: string }) =>
      saveAIKey(authToken!, provider, key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AI_KEY_QUERY_KEY] })
      setKeyInput('')
      setTestMsg({ type: 'success', text: 'Key saved successfully.' })
      setTimeout(() => setTestMsg(null), 3000)
    },
    onError: (err: Error) => {
      setTestMsg({ type: 'error', text: err.message || 'Failed to save key.' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (provider: string) => deleteAIKey(authToken!, provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AI_KEY_QUERY_KEY] })
      setTestMsg({ type: 'success', text: 'Key removed.' })
      setTimeout(() => setTestMsg(null), 3000)
    },
    onError: (err: Error) => {
      setTestMsg({ type: 'error', text: err.message || 'Failed to remove key.' })
    },
  })

  const handleTest = async () => {
    if (!authToken || !keyInput.trim()) return
    setIsTesting(true)
    setTestMsg(null)
    try {
      await testAIKey(authToken, selectedProvider, keyInput.trim())
      setTestMsg({ type: 'success', text: 'Key is valid and working.' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Key validation failed.'
      setTestMsg({ type: 'error', text: message })
    } finally {
      setIsTesting(false)
    }
  }

  const handleSave = () => {
    if (!keyInput.trim()) return
    saveMutation.mutate({ provider: selectedProvider, key: keyInput.trim() })
  }

  const currentProviderHasKey =
    selectedProvider === 'openai' ? keyStatus?.hasOpenaiKey : keyStatus?.hasGeminiKey

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
      <h2 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
        <Key size={16} className="text-amber-500" />
        Your AI Provider Key
      </h2>
      <p className="text-xs text-gray-500 mb-2">
        Supply your own API key so your AI requests are billed to your account.
        The key is encrypted at rest and never returned in API responses.
      </p>
      <p className="mb-4">
        <SetupGuideLink href={SETUP_GUIDE_SECTIONS.geminiKey} label="How to create a Google Gemini key" />
      </p>

      {/* Provider selector */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        {PROVIDERS.map((p) => (
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

      {/* Current status badge */}
      <div className="flex items-center gap-2 mb-4">
        {currentProviderHasKey ? (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-green-700 bg-green-50 border border-green-200 px-2.5 py-1 rounded-full">
            <CheckCircle size={12} />
            Key saved
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-500 bg-gray-100 px-2.5 py-1 rounded-full">
            No key saved
          </span>
        )}
      </div>

      {/* Key input */}
      <div className="space-y-3">
        <div>
          <label className="block text-xs font-semibold text-gray-700 mb-1">
            {PROVIDERS.find((p) => p.value === selectedProvider)?.label} API Key
          </label>
          <input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder={PROVIDERS.find((p) => p.value === selectedProvider)?.placeholder}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-amber-500 focus:border-amber-500"
            autoComplete="off"
          />
        </div>

        {testMsg && (
          <div
            className={`flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm ${
              testMsg.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-700'
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}
          >
            {testMsg.type === 'success' ? (
              <CheckCircle size={15} className="flex-shrink-0 mt-0.5" />
            ) : (
              <AlertCircle size={15} className="flex-shrink-0 mt-0.5" />
            )}
            {testMsg.text}
          </div>
        )}

        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => void handleTest()}
            disabled={isTesting || !keyInput.trim()}
            className="flex items-center gap-1.5 border border-gray-300 text-gray-700 rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
          >
            {isTesting ? <Loader size={14} className="animate-spin" /> : <Key size={14} />}
            {isTesting ? 'Testing…' : 'Test key'}
          </button>

          <button
            onClick={handleSave}
            disabled={saveMutation.isPending || !keyInput.trim()}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
          >
            {saveMutation.isPending ? (
              <Loader size={14} className="animate-spin" />
            ) : (
              <CheckCircle size={14} />
            )}
            {saveMutation.isPending ? 'Saving…' : 'Save key'}
          </button>

          {currentProviderHasKey && (
            <button
              onClick={() => deleteMutation.mutate(selectedProvider)}
              disabled={deleteMutation.isPending}
              className="flex items-center gap-1.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-red-100 disabled:opacity-50"
            >
              <Trash2 size={14} />
              {deleteMutation.isPending ? 'Removing…' : 'Remove key'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
