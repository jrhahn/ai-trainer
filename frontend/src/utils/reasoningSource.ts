import type { ReasoningSource } from '../services/ai'

// Shared presentation for the three knowledge-source categories (issue #377),
// so every surface that explains coaching advice — the race readiness card and
// the AI chat "Why this answer?" disclosure — labels the origin of a claim
// identically: a personal observation about the athlete, established sports
// science, or the coach's read of the numbers.
export const REASONING_SOURCE_META: Record<ReasoningSource, { label: string }> = {
  personal_observation: { label: 'Personal observation' },
  scientific_evidence: { label: 'Scientific evidence' },
  coach_inference: { label: 'Coach inference' },
}

// The label in front of a reason. Plain words, not a coloured badge: one
// accent on every page (owner's review, 2026-10-24).
export const REASONING_BADGE_CLASS = "font-medium text-gray-700 after:content-[':']"
