import { apiFetch } from './api'

/** An offer under a coach reply, as the server names it (ai-trainer-ops#338 T1). */
export type OfferKind = 'raceOffer' | 'raceProposal' | 'profileProposal'
export type OfferOutcome = 'accepted' | 'dismissed'

/** The athlete answered an offer under a reply: it is not offered again (ai-trainer-ops#338 T1). */
export async function resolveChatOfferRemote(
  token: string,
  messageId: string,
  kind: OfferKind,
  outcome: OfferOutcome,
): Promise<void> {
  await apiFetch(
    `/users/me/chat/${encodeURIComponent(messageId)}/offers/${kind}/resolve`,
    { token, method: 'POST', body: { outcome } },
  )
}

/**
 * Best effort: the answer itself (the race added, the profile changed) is done
 * by then, and a failed record only means the offer shows again after a reload.
 */
export function rememberOfferAnswer(
  token: string | null | undefined,
  messageId: string | undefined,
  kind: OfferKind,
  outcome: OfferOutcome,
): void {
  if (!token || !messageId) return
  resolveChatOfferRemote(token, messageId, kind, outcome).catch(() => undefined)
}
