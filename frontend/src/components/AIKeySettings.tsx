import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Key, Trash2, CheckCircle, AlertCircle, Loader } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import SetupGuideLink from './SetupGuideLink'
import { SETUP_GUIDE_SECTIONS } from '../utils/links'
import { localeTag, useLanguage, useT } from '../i18n'
import {
  fetchAIKeyStatus,
  saveAIKey,
  deleteAIKey,
  testAIKey,
  type AIKeyStatus,
} from '../services/user'

const AI_KEY_QUERY_KEY = 'ai-key-status'

// No model names here on purpose (#691). They were "GPT-4o" and "Gemini 2.5
// Flash" — the second wrong for over a release, and disagreeing with the copy
// on the settings page itself, which claimed "Gemini 2.0 Flash". The backend
// reports what it actually runs; see `hintFor` below.
const PROVIDERS = [
  { value: 'openai', label: 'OpenAI', placeholder: 'sk-...' },
  { value: 'gemini', label: 'Google Gemini', placeholder: 'AIza...' },
] as const

type Provider = (typeof PROVIDERS)[number]['value']

export default function AIKeySettings() {
  const { authToken } = useAppStore(useShallow((s) => ({ authToken: s.authToken })))
  const queryClient = useQueryClient()
  const t = useT()
  const language = useLanguage()

  // Null until the athlete picks one: the provider the account runs on is the
  // default, not OpenAI (ai-trainer-ops#93), which the landing page never names.
  const [pickedProvider, setSelectedProvider] = useState<Provider | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [testMsg, setTestMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [isTesting, setIsTesting] = useState(false)

  const { data: keyStatus } = useQuery<AIKeyStatus>({
    queryKey: [AI_KEY_QUERY_KEY],
    queryFn: () => fetchAIKeyStatus(authToken!),
    enabled: !!authToken,
  })
  const selectedProvider: Provider = pickedProvider ?? (keyStatus?.provider === 'openai' ? 'openai' : 'gemini')
  // The app's own key pays for this athlete: no key of their own, and the
  // server does not need one (`keyRequired` is false only then).
  const onSharedKey =
    keyStatus?.keyRequired === false && !keyStatus.hasOpenaiKey && !keyStatus.hasGeminiKey

  const saveMutation = useMutation({
    mutationFn: ({ provider, key }: { provider: string; key: string }) =>
      saveAIKey(authToken!, provider, key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AI_KEY_QUERY_KEY] })
      setKeyInput('')
      setTestMsg({ type: 'success', text: t('The key works and is saved.') })
      setTimeout(() => setTestMsg(null), 3000)
    },
    onError: (err: Error) => {
      setTestMsg({ type: 'error', text: t(err.message || 'Failed to save key.') })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (provider: string) => deleteAIKey(authToken!, provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [AI_KEY_QUERY_KEY] })
      setTestMsg({ type: 'success', text: t('Key removed.') })
      setTimeout(() => setTestMsg(null), 3000)
    },
    onError: (err: Error) => {
      setTestMsg({ type: 'error', text: t(err.message || 'Failed to remove key.') })
    },
  })

  // One action, test then save (ai-trainer-ops#93). A key is a secret the
  // provider has to accept, so it is the one setting that does not save on its
  // own; and a key that fails the test is never stored.
  const handleTestAndSave = async () => {
    const key = keyInput.trim()
    if (!authToken || !key) return
    setIsTesting(true)
    setTestMsg(null)
    try {
      await testAIKey(authToken, selectedProvider, key)
    } catch (err) {
      const message = t(err instanceof Error ? err.message : 'Key validation failed.')
      setTestMsg({ type: 'error', text: message })
      return
    } finally {
      setIsTesting(false)
    }
    saveMutation.mutate({ provider: selectedProvider, key })
  }

  const currentProviderHasKey =
    selectedProvider === 'openai' ? keyStatus?.hasOpenaiKey : keyStatus?.hasGeminiKey

  /** The model the backend reports for *provider*, or nothing to show. */
  const hintFor = (provider: Provider) =>
    provider === 'openai' ? keyStatus?.openaiModel : keyStatus?.geminiModel

  return (
    // The id is the anchor target of the pointer in the AI Provider section
    // above; scroll-mt keeps the heading clear of the sticky header on jump.
    <div
      id="your-ai-provider-key"
      className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6 scroll-mt-20"
    >
      <h3 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
        <Key size={16} className="text-amber-500" />
        {t('Your AI Provider Key')}
      </h3>
      {onSharedKey ? (
        <p className="text-sm text-gray-700 mb-2" data-testid="shared-key">
          {t('The coach runs on this app’s key, so you need no key of your own. Add one only if you want your requests billed to your own account. A key is encrypted at rest and never returned in API responses.')}
        </p>
      ) : (
        <p className="text-xs text-gray-500 mb-2">
          {t('Supply your own API key so your AI requests are billed to your account. The key is encrypted at rest and never returned in API responses.')}
        </p>
      )}
      <p className="mb-4">
        <SetupGuideLink href={SETUP_GUIDE_SECTIONS.geminiKey} label={t('How to create a Google Gemini key')} />
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
            <span className="text-xs text-gray-500 font-mono">{hintFor(p.value) ?? '—'}</span>
          </button>
        ))}
      </div>

      {/* The free allowance while the owner's key pays (ai-trainer-ops#79). */}
      {keyStatus?.allowanceTokens ? (
        <p className="mb-4 text-xs text-gray-600" data-testid="free-allowance">
          {t('Free coaching allowance: {used}k of {total}k tokens used in the last {days} days. With your own key there is no limit.', {
            used: Math.round((keyStatus.allowanceUsed ?? 0) / 1000).toLocaleString(localeTag(language)),
            total: Math.round(keyStatus.allowanceTokens / 1000).toLocaleString(localeTag(language)),
            days: keyStatus.allowanceWindowDays ?? 30,
          })}
        </p>
      ) : null}

      {/* Current status badge */}
      <div className="flex items-center gap-2 mb-4">
        {currentProviderHasKey ? (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-green-700 bg-green-50 border border-green-200 px-2.5 py-1 rounded-full">
            <CheckCircle size={12} />
            {t('Key saved')}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-500 bg-gray-100 px-2.5 py-1 rounded-full">
            {t('No key saved')}
          </span>
        )}
      </div>

      {/* Key input */}
      <div className="space-y-3">
        <div>
          <label className="block text-xs font-semibold text-gray-700 mb-1">
            {t('{provider} API Key', { provider: PROVIDERS.find((p) => p.value === selectedProvider)?.label ?? '' })}
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
              <CheckCircle size={15} className="shrink-0 mt-0.5" />
            ) : (
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
            )}
            {testMsg.text}
          </div>
        )}

        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => void handleTestAndSave()}
            disabled={isTesting || saveMutation.isPending || !keyInput.trim()}
            className="flex items-center gap-1.5 bg-amber-500 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
          >
            {isTesting || saveMutation.isPending ? (
              <Loader size={14} className="animate-spin" />
            ) : (
              <Key size={14} />
            )}
            {isTesting ? t('Testing…') : saveMutation.isPending ? t('Saving…') : t('Test and save key')}
          </button>

          {currentProviderHasKey && (
            <button
              onClick={() => deleteMutation.mutate(selectedProvider)}
              disabled={deleteMutation.isPending}
              className="flex items-center gap-1.5 bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-2 text-sm font-semibold hover:bg-red-100 disabled:opacity-50"
            >
              <Trash2 size={14} />
              {deleteMutation.isPending ? t('Removing…') : t('Remove key')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
