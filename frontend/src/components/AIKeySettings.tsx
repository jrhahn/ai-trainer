import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import SetupGuideLink from './SetupGuideLink'
import { button, chip, fieldLabel, input, note, textButton } from './ui'
import { setupGuideSection } from '../utils/links'
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
  const { authToken, isExpertMode } = useAppStore(
    useShallow((s) => ({ authToken: s.authToken, isExpertMode: s.isExpertMode })),
  )
  const queryClient = useQueryClient()
  const t = useT()
  const language = useLanguage()

  // Null until the athlete picks one: the provider the account runs on is the
  // default, not OpenAI (ai-trainer-ops#93), which the landing page never names.
  const [pickedProvider, setSelectedProvider] = useState<Provider | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [testMsg, setTestMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [isTesting, setIsTesting] = useState(false)

  const { data: keyStatus, isError: statusFailed } = useQuery<AIKeyStatus>({
    queryKey: [AI_KEY_QUERY_KEY],
    queryFn: () => fetchAIKeyStatus(authToken!),
    enabled: !!authToken,
  })
  // The app's own key pays for this athlete: no key of their own, and the
  // server does not need one (`keyRequired` is false only then).
  const onSharedKey =
    keyStatus?.keyRequired === false && !keyStatus.hasOpenaiKey && !keyStatus.hasGeminiKey
  // On the shared key a key of their own is an option, not a step: folded, and
  // Gemini only, the provider the shared key runs. Another provider is an
  // expert's choice (ai-trainer-ops#217). Folded while the status loads too, so
  // the providers do not flash up first (review of #1049).
  // A status that failed to load folds nothing: an athlete with their own key
  // of another provider must still reach it.
  const folded = !isExpertMode && ((!keyStatus && !statusFailed) || onSharedKey)
  const selectedProvider: Provider = folded
    ? 'gemini'
    : (pickedProvider ?? (keyStatus?.provider === 'openai' ? 'openai' : 'gemini'))

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

  const guideLink = (
    <p className="mb-4">
      <SetupGuideLink href={setupGuideSection(language, 'geminiKey')} label={t('How to create a Google Gemini key')} />
    </p>
  )
  // The allowance, the state and the field: folded on the shared key.
  const keyForm = (
    <>
      {keyStatus?.allowanceTokens ? (
        <p className="mb-4 text-sm text-gray-500" data-testid="free-allowance">
          {t('Free coaching allowance: {used}k of {total}k tokens used in the last {days} days. With your own key there is no limit.', {
            used: Math.round((keyStatus.allowanceUsed ?? 0) / 1000).toLocaleString(localeTag(language)),
            total: Math.round(keyStatus.allowanceTokens / 1000).toLocaleString(localeTag(language)),
            days: keyStatus.allowanceWindowDays ?? 30,
          })}
        </p>
      ) : null}

      {/* The state in one grey line, with the way to remove it beside it:
          no green badge, no red button (owner's review, 2026-10-23). */}
      <div className="mb-4 flex items-center gap-3 text-sm text-gray-500">
        <span>{currentProviderHasKey ? t('Key saved') : t('No key saved')}</span>
        {currentProviderHasKey && (
          <button
            onClick={() => deleteMutation.mutate(selectedProvider)}
            disabled={deleteMutation.isPending}
            className="font-medium text-gray-500 hover:text-red-600 disabled:opacity-50"
          >
            {deleteMutation.isPending ? t('Removing…') : t('Remove key')}
          </button>
        )}
      </div>

      {/* Key input */}
      <div className="space-y-3">
        <div>
          <label className={fieldLabel}>
            {t('{provider} API Key', { provider: PROVIDERS.find((p) => p.value === selectedProvider)?.label ?? '' })}
          </label>
          <input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder={PROVIDERS.find((p) => p.value === selectedProvider)?.placeholder}
            className={input}
            autoComplete="off"
          />
        </div>

        {testMsg && <p className={note(testMsg.type === 'success')}>{testMsg.text}</p>}

        <button
          onClick={() => void handleTestAndSave()}
          disabled={isTesting || saveMutation.isPending || !keyInput.trim()}
          className={button}
        >
          {isTesting ? t('Testing…') : saveMutation.isPending ? t('Saving…') : t('Test and save key')}
        </button>
      </div>
    </>
  )

  return (
    // The id is the anchor target of the pointer in the AI Provider section
    // above; scroll-mt keeps the heading clear of the sticky header on jump.
    <div
      id="your-ai-provider-key"
      className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6 scroll-mt-20"
    >
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Your AI Provider Key')}</h3>
      {/* Nothing until the status says which sentence is true. */}
      {!keyStatus && !statusFailed ? null : onSharedKey ? (
        <p className="text-sm text-gray-500 mb-2" data-testid="shared-key">
          {t('The coach runs on this app’s key, so you need no key of your own. Add one only if you want your requests billed to your own account. A key is encrypted at rest and never returned in API responses.')}
        </p>
      ) : (
        <p className="text-sm text-gray-500 mb-2">
          {t('Supply your own API key so your AI requests are billed to your account. The key is encrypted at rest and never returned in API responses.')}
        </p>
      )}
      {!folded && guideLink}

      {/* Provider selector: the same chips as the language and the training
          answers. The model each one runs is an expert's detail. */}
      {!folded && (
        <div className="mb-4 flex flex-wrap gap-2">
          {PROVIDERS.map((p) => (
            <button
              key={p.value}
              type="button"
              aria-pressed={selectedProvider === p.value}
              onClick={() => setSelectedProvider(p.value)}
              className={chip(selectedProvider === p.value)}
            >
              {p.label}
              {isExpertMode && hintFor(p.value) && <span className="ml-1.5 text-sm text-gray-500">{hintFor(p.value)}</span>}
            </button>
          ))}
        </div>
      )}

      {folded ? (
        <details>
          <summary className={`${textButton} cursor-pointer`}>{t('Own key (optional)')}</summary>
          <div className="mt-3">
            {guideLink}
            {keyForm}
          </div>
        </details>
      ) : (
        keyForm
      )}
    </div>
  )
}
