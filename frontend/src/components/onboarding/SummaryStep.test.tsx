import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SummaryStep from './SummaryStep'
import { useAppStore } from '../../store/useAppStore'
import type { OnboardingForm } from '../../onboarding/form'

const FORM: OnboardingForm = {
  trainingGoal: 'general_fitness',
  raceDate: '',
  raceDescription: '',
  assessmentMethod: 'manual',
  sports: ['cycling'],
  trainingDays: ['tuesday', 'saturday'],
  currentFTP: '',
  fitnessLevel: 'intermediate',
  maxHeartRate: '',
}

function renderSummary(form: Partial<OnboardingForm>) {
  render(<SummaryStep form={{ ...FORM, ...form }} error="" loading={false} onGenerate={vi.fn()} />)
}

afterEach(() => useAppStore.setState({ uiLanguage: 'en' }))

// Review of 2026-11-01, ai-trainer-ops#338 A16.
describe('SummaryStep', () => {
  it('names the goal with the words the goal step offered', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    renderSummary({})
    expect(screen.getByText('Allgemeine Fitness')).toBeInTheDocument()
    expect(screen.queryByText('allgemeine Fitness')).not.toBeInTheDocument()
  })

  it('writes the race date as a date, not as 2027-05-10', () => {
    useAppStore.setState({ uiLanguage: 'de' })
    renderSummary({ trainingGoal: 'race', raceDate: '2027-05-10' })
    expect(screen.getByText('Rennvorbereitung')).toBeInTheDocument()
    expect(screen.queryByText('2027-05-10')).not.toBeInTheDocument()
    expect(screen.getByText(/10\. Mai 2027/)).toBeInTheDocument()
  })
})
