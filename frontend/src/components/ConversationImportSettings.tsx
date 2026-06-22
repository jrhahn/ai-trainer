import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, MessageSquareText, Sparkles, X } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { extractAthleteFacts, type AthleteFactCandidate } from '../services/ai'
import { observeAthleteMemoryFact } from '../services/user'
import { ATHLETE_TRAITS_QUERY_KEY } from './AthleteTraitsSettings'

/** Turn a category slug (e.g. "coaching_risk") into a readable label. */
function formatCategory(category: string): string {
  const cleaned = category.replace(/_/g, ' ').trim()
  if (!cleaned) return 'General'
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1)
}

function CandidateRow({
  candidate,
  token,
  onResolved,
}: {
  candidate: AthleteFactCandidate
  token: string
  onResolved: () => void
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState('')

  const acceptMutation = useMutation({
    mutationFn: () =>
      observeAthleteMemoryFact(token, {
        fact: candidate.fact,
        category: candidate.category,
        sourceSnippet: candidate.sourceSnippet,
        confidence: candidate.confidence,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [ATHLETE_TRAITS_QUERY_KEY] })
      onResolved()
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to save trait'),
  })

  return (
    <li className="border border-gray-100 rounded-xl px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-gray-900">{candidate.fact}</p>
          <p className="text-[11px] text-gray-400 mt-1">
            {formatCategory(candidate.category)}
            {' · '}confidence {Math.round(candidate.confidence * 100)}%
          </p>
          {candidate.sourceSnippet && (
            <p className="mt-1 text-[11px] italic text-gray-400">
              “{candidate.sourceSnippet}”
            </p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => acceptMutation.mutate()}
            disabled={acceptMutation.isPending}
            title="Accept and store this trait"
            aria-label="Accept candidate"
            className="p-1.5 text-gray-400 hover:text-green-600 disabled:opacity-50"
          >
            <Check size={15} />
          </button>
          <button
            onClick={onResolved}
            disabled={acceptMutation.isPending}
            title="Reject this candidate"
            aria-label="Reject candidate"
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <X size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-xs text-red-600 mt-1.5">{error}</p>}
    </li>
  )
}

export default function ConversationImportSettings() {
  const authToken = useAppStore((s) => s.authToken)
  const [transcript, setTranscript] = useState('')
  const [candidates, setCandidates] = useState<AthleteFactCandidate[] | null>(null)
  const [error, setError] = useState('')

  const extractMutation = useMutation({
    mutationFn: () => extractAthleteFacts(transcript, authToken!),
    onSuccess: (result) => {
      setCandidates(result)
      setError('')
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to extract traits'),
  })

  // Remove a candidate from the review list once accepted or rejected.
  const resolveCandidate = (index: number) =>
    setCandidates((prev) => (prev ? prev.filter((_, i) => i !== index) : prev))

  const canExtract = !!authToken && transcript.trim().length > 0 && !extractMutation.isPending

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
      <h2 className="flex items-center gap-1.5 text-base font-bold text-gray-900 mb-1">
        <MessageSquareText size={16} /> Import Coach Conversations
      </h2>
      <p className="text-xs text-gray-500 mb-4">
        Paste a past coaching conversation. The coach will suggest durable traits
        and preferences to learn — you choose which to keep before anything is
        saved.
      </p>

      <textarea
        aria-label="Conversation transcript"
        value={transcript}
        onChange={(e) => setTranscript(e.target.value)}
        rows={6}
        placeholder="Paste your historical coaching conversation here…"
        className="w-full text-sm border border-gray-200 rounded-xl px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-200"
      />

      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={() => extractMutation.mutate()}
          disabled={!canExtract}
          className="flex items-center gap-1.5 bg-blue-600 text-white rounded-lg px-4 py-2 text-sm font-semibold hover:bg-blue-700 disabled:opacity-50"
        >
          <Sparkles size={15} />
          {extractMutation.isPending ? 'Extracting…' : 'Extract traits'}
        </button>
        {candidates && candidates.length === 0 && (
          <span className="text-xs text-gray-400">
            No durable traits found in that conversation.
          </span>
        )}
      </div>

      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}

      {candidates && candidates.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1.5">
            Review candidates ({candidates.length})
          </h3>
          <ul className="space-y-2">
            {candidates.map((candidate, index) => (
              <CandidateRow
                key={`${candidate.fact}-${index}`}
                candidate={candidate}
                token={authToken!}
                onResolved={() => resolveCandidate(index)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
