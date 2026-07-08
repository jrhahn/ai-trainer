import type { ReasoningSource } from '../services/ai'

// Shared presentation for the three knowledge-source categories (issue #377),
// so every surface that explains coaching advice — the race readiness card and
// the AI chat "Why this advice?" disclosure — labels the origin of a claim
// identically: a personal observation about the athlete, established sports
// science, or the coach's read of the numbers.
export const REASONING_SOURCE_META: Record<
  ReasoningSource,
  { label: string; className: string }
> = {
  personal_observation: {
    label: 'Personal observation',
    className: 'bg-purple-50 text-purple-600 border border-purple-100',
  },
  scientific_evidence: {
    label: 'Scientific evidence',
    className: 'bg-blue-50 text-blue-600 border border-blue-100',
  },
  coach_inference: {
    label: 'Coach inference',
    className: 'bg-gray-100 text-gray-600 border border-gray-200',
  },
}

// Base styling for a source badge; combine with a source's `className`.
export const REASONING_BADGE_CLASS =
  'rounded px-1 py-px text-[9px] font-medium uppercase tracking-wide'
