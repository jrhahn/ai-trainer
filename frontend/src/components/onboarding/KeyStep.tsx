import { AlertTriangle, CheckCircle, Link, Loader2 } from 'lucide-react'
import { useT } from '../../i18n'
import type { AiKeyStep } from '../../hooks/useAiKeyStep'

/** The key step (ai-trainer-ops#41). Only reachable when the backend says this
 *  athlete must supply a key, so the copy can state that plainly instead of
 *  hedging (ai-trainer-ops#274: out of `OnboardingPage`). */
export default function KeyStep({ aiKey }: { aiKey: AiKeyStep }) {
  const t = useT()
  return (
    <div>
      <h2 className="text-xl font-bold text-gray-900 mb-1">{t('Add your Gemini key')}</h2>
      {aiKey.keyPrompt && (
        <p className="mb-3 flex items-start gap-1.5 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          {t(aiKey.keyPrompt)}
        </p>
      )}
      <p className="text-sm text-gray-500 mb-4">
        {t('This deployment does not provide a model, so your coach runs on your own Google Gemini key. It stays on this server, encrypted at rest, and is used only for your own plans and chats.')}
      </p>

      <a
        href="https://aistudio.google.com/apikey"
        target="_blank"
        rel="noreferrer"
        className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-amber-600 hover:text-amber-700"
      >
        <Link size={14} aria-hidden="true" />
        {t('Get a key — it takes about two minutes')}
      </a>

      <label
        htmlFor="onboarding-ai-key"
        className="mb-1 block text-sm font-medium text-gray-700"
      >
        {t('Gemini API key')}
      </label>
      <input
        id="onboarding-ai-key"
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={aiKey.keyInput}
        onChange={(e) => aiKey.editKey(e.target.value)}
        placeholder="AIza…"
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-amber-500 focus:ring-amber-500"
      />

      {aiKey.keyError && (
        <p className="mt-2 flex items-start gap-1.5 text-sm text-red-600">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          {t(aiKey.keyError)}
        </p>
      )}
      {aiKey.keyTested && !aiKey.keyError && (
        <p className="mt-2 flex items-center gap-1.5 text-sm text-emerald-700">
          <CheckCircle size={14} className="shrink-0" aria-hidden="true" />
          {t('The key works.')}
        </p>
      )}

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={() => void aiKey.testKey()}
          disabled={!aiKey.keyInput.trim() || aiKey.keyTesting || aiKey.keySaving}
          className="flex-1 rounded-xl border border-gray-300 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
        >
          {aiKey.keyTesting ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              {t('Testing…')}
            </span>
          ) : (
            t('Test key')
          )}
        </button>
        <button
          type="button"
          onClick={() => void aiKey.saveKey()}
          disabled={!aiKey.keyInput.trim() || aiKey.keyTesting || aiKey.keySaving}
          className="flex-1 rounded-xl bg-amber-500 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-amber-600 disabled:opacity-50"
        >
          {aiKey.keySaving ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              {t('Saving…')}
            </span>
          ) : (
            t('Save and continue')
          )}
        </button>
      </div>

      <p className="mt-3 text-xs text-gray-400">
        {t('You can change or remove it later under Settings → AI Provider.')}
      </p>
    </div>
  )
}
