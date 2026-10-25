import { useEffect, useRef, useState } from 'react'
import { fetchAIKeyStatus, saveAIKey, testAIKey } from '../services/user'

/** Onboarding's key step (ai-trainer-ops#41): whether this athlete needs a key of
 *  their own, and the form that stores one (ai-trainer-ops#274: out of
 *  `OnboardingPage`). `onSaved` runs once the backend says the key is enough. */
export function useAiKeyStep(authToken: string | null, onSaved: () => void) {
  // Whether this athlete has to bring their own key, answered by the backend
  // rather than guessed from `hasGeminiKey` — the client cannot see whether
  // admin-key fallback is on or whether the owner set a global key
  // (ai-trainer-ops#41).
  //
  // `false` until the answer arrives, so a hosted athlete is never shown a step
  // that then vanishes. If the request fails it stays `false` and onboarding
  // behaves as it did before: the 402 from "Generate" is caught below and sends
  // the athlete to the key step anyway, so a failed probe costs a detour rather
  // than the dead end.
  const [keyRequired, setKeyRequired] = useState(false)
  // Whether the probe has answered yet. `keyRequired` starts false, which is
  // the right default to *render* but not something to make decisions on: the
  // difference between "no key is needed" and "we have not asked yet" matters
  // for a resumed session and for the generate shortcut below. Raised in review.
  const [keyStatusKnown, setKeyStatusKnown] = useState(false)
  // Whether the server has told us directly, rather than been asked in advance.
  //
  // Two things count: a 402 from `generateTrainingPlan`, which is proof a key
  // is needed, and the `keyRequired` that `PUT /users/me/ai-key` recomputes,
  // which is proof one is not. The on-mount probe is a forecast, and once we
  // hold either kind of proof a forecast still in flight must not overrule it.
  //
  // Both directions were raised in review. The 402 one reinstates the loop this
  // change removes: the probe resolves `false`, the redirect effect fires, and
  // the athlete is carried off the key step onto a summary about to 402 again.
  // The save one is milder — a stale `true` landing after a successful save
  // leaves the header reading "Step 6 of 6" behind a key step the athlete has
  // already finished — and the first version of this invited it, by *clearing*
  // the ref on save as though a save were less authoritative than a failure.
  //
  // A ref, not state: nothing renders from it, and it has to be readable by a
  // promise that resolves after the render it was created in.
  const keyStateIsAuthoritative = useRef(false)
  const [keyInput, setKeyInput] = useState('')
  const [keyTesting, setKeyTesting] = useState(false)
  const [keySaving, setKeySaving] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [keyTested, setKeyTested] = useState(false)
  // Why the athlete is suddenly on this step, when they got here by pressing
  // "Generate" rather than by walking the wizard. Without it the redirect below
  // swaps the summary — and the error that explained it — for a form asking
  // for a credential, with nothing connecting the two.
  const [keyPrompt, setKeyPrompt] = useState('')
  useEffect(() => {
    if (!authToken) return
    let cancelled = false
    fetchAIKeyStatus(authToken)
      .then((status) => {
        if (cancelled) return
        // Never let a forecast overwrite what the server has already said.
        if (!keyStateIsAuthoritative.current) setKeyRequired(Boolean(status.keyRequired))
        setKeyStatusKnown(true)
      })
      .catch(() => {
        // Deliberately silent. Nothing the athlete can do about it, and the
        // generate path recovers on its own; a banner here would be noise on
        // the welcome screen of a flow that still works.
        //
        // Counted as "known" all the same, so a failed probe does not leave a
        // resumed session parked on the key step forever. The cost is a BYOK
        // athlete being sent to the summary and bounced back by the 402 — a
        // detour that heals itself, against a screen with no way off it.
        if (!cancelled) setKeyStatusKnown(true)
      })
    return () => {
      cancelled = true
    }
  }, [authToken])

  /** Check a key against the provider without storing it. */
  const testKey = async () => {
    if (!authToken) return
    setKeyTesting(true)
    setKeyError('')
    setKeyTested(false)
    try {
      await testAIKey(authToken, 'gemini', keyInput.trim())
      setKeyTested(true)
    } catch (e) {
      setKeyError(e instanceof Error ? e.message : 'Could not check the key.')
    } finally {
      setKeyTesting(false)
    }
  }

  /** Store the key, then move on — but only if the backend agrees it is enough.
   *
   * `PUT /users/me/ai-key` answers with a fresh `keyRequired`, so a key that was
   * accepted for a provider this deployment cannot reach does not advance the
   * flow. Trusting a 200 here would put the athlete back on "Generate" with the
   * same 402, which is the loop ai-trainer-ops#41 describes.
   */
  const saveKey = async () => {
    if (!authToken) return
    setKeySaving(true)
    setKeyError('')
    try {
      const status = await saveAIKey(authToken, 'gemini', keyInput.trim())
      if (status.keyRequired) {
        setKeyError('Saved, but this deployment still cannot reach a model with it.')
        return
      }
      // Set, not cleared. The backend has just recomputed `keyRequired` for
      // this account and said no key is needed — which is the same kind of
      // answer as the 402, pointing the other way. Clearing it here let a probe
      // that was still in flight resolve with a stale `true` afterwards.
      keyStateIsAuthoritative.current = true
      setKeyRequired(false)
      setKeyInput('')
      setKeyTested(false)
      setKeyPrompt('')
      onSaved()
    } catch (e) {
      setKeyError(e instanceof Error ? e.message : 'Could not save the key.')
    } finally {
      setKeySaving(false)
    }
  }

  /** "Generate" came back with a 402: proof that a key is needed, which no
   *  probe still in flight may overrule. `prompt` says why the athlete is here. */
  const requireKey = (prompt: string) => {
    keyStateIsAuthoritative.current = true
    setKeyRequired(true)
    setKeyPrompt(prompt)
  }

  /** A key that was tested and then edited has not been tested. */
  const editKey = (value: string) => {
    setKeyInput(value)
    setKeyTested(false)
    setKeyError('')
  }

  return {
    keyRequired,
    keyStatusKnown,
    keyInput,
    keyTesting,
    keySaving,
    keyError,
    keyTested,
    keyPrompt,
    editKey,
    testKey,
    saveKey,
    requireKey,
  }
}

export type AiKeyStep = ReturnType<typeof useAiKeyStep>
