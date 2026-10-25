import { useQuery } from '@tanstack/react-query'
import { useShallow } from 'zustand/shallow'
import { useAppStore, type AiProvider } from '../../store/useAppStore'
import { useAuthToken } from '../../hooks/useAuthToken'
import { useSaveStatus } from '../../hooks/useSaveStatus'
import { fetchAIKeyStatus, updateCurrentUser, type AIKeyStatus } from '../../services/user'
import { AI_KEY_QUERY_KEY } from '../../queries/keys'
import { slots, useT } from '../../i18n'
import SaveStatus from '../SaveStatus'

/** Settings → Coach & AI, expert mode: which server-side model the coach runs
 *  on (ai-trainer-ops#274: out of `SettingsPage`). */
export default function AiProviderCard() {
  const t = useT()
  const authToken = useAuthToken()
  const { aiProvider, setAiProvider } = useAppStore(
    useShallow((s) => ({ aiProvider: s.aiProvider, setAiProvider: s.setAiProvider })),
  )
  const providerStatus = useSaveStatus()

  const chooseProvider = (provider: AiProvider) => {
    if (!authToken || provider === aiProvider) return
    void providerStatus.run(async () => {
      await updateCurrentUser(authToken, { aiProvider: provider })
      setAiProvider(provider)
    })
  }

  // The hints used to be hard-coded here as "GPT-4o mini" / "Gemini 2.0 Flash"
  // — stale, and disagreeing with AIKeySettings, which claimed "Gemini 2.5
  // Flash" for the same backend. Both were wrong: it has been
  // gemini-3.5-flash-lite since #511. The backend now reports what it runs
  // (#691). Same query key as AIKeySettings, so react-query serves both from
  // one request.
  const { data: keyStatus } = useQuery<AIKeyStatus>({
    queryKey: [AI_KEY_QUERY_KEY],
    queryFn: () => fetchAIKeyStatus(authToken),
    enabled: !!authToken,
  })

  const providers: { value: AiProvider; label: string; hint?: string; placeholder: string }[] = [
    { value: 'openai', label: 'OpenAI', hint: keyStatus?.openaiModel, placeholder: 'sk-...' },
    { value: 'gemini', label: 'Google Gemini', hint: keyStatus?.geminiModel, placeholder: 'AIza...' },
  ]

  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('AI Provider')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Choose which server-side model powers your training plan and coach chat.')}
      </p>

      <div className="grid grid-cols-2 gap-3 mb-4">
        {providers.map((p) => (
          <button
            key={p.value}
            type="button"
            onClick={() => chooseProvider(p.value)}
            aria-pressed={aiProvider === p.value}
            className={`flex flex-col items-start px-4 py-3 rounded-xl border-2 text-left transition-all ${
              aiProvider === p.value
                ? 'border-amber-500 bg-amber-50'
                : 'border-gray-200 hover:border-amber-300'
            }`}
          >
            <span className="font-semibold text-sm text-gray-900">{p.label}</span>
            <span className="text-sm text-gray-500">{p.hint ?? '—'}</span>
          </button>
        ))}
      </div>

      {/*
        This used to warn that "the browser no longer stores or sends provider
        API keys". That predates BYOK and is now the opposite of true — the
        "Your AI Provider Key" section at the bottom of this page sends one.
        Read in order, the page told you there was nothing to enter and then
        offered the field 470 lines later, which is exactly how long it took
        someone to give up looking for it.
      */}
      <div className="mb-4">
        <p className="text-sm text-gray-500">
          {slots(
            t("This picks the model only. Requests run on the server's key by default — to have them billed to your own account instead, add a key under {link} above."),
            {
              link: (
                <a key="link" href="#your-ai-provider-key" className="font-medium text-amber-600 hover:underline">
                  {t('Your AI Provider Key')}
                </a>
              ),
            },
          )}
        </p>
      </div>

      <SaveStatus state={providerStatus.state} />
    </div>
  )
}
