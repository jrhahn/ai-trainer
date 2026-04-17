import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FitnessMetricsCard from './FitnessMetricsCard'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const mockUpdateCurrentUser = vi.hoisted(() => vi.fn())
vi.mock('../services/user', () => ({ updateCurrentUser: mockUpdateCurrentUser }))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

beforeEach(() => {
  useAppStore.getState().resetAll()
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue({})
})

describe('FitnessMetricsCard', () => {
  it('shows "Not set" for all metrics when no values are set', () => {
    useAppStore.setState({ authToken: 'tok', userProfile: baseProfile })
    render(<FitnessMetricsCard />)
    // All four metrics should show "Not set"
    const notSetElements = screen.getAllByText('Not set')
    expect(notSetElements.length).toBeGreaterThanOrEqual(4)
  })

  it('displays existing metric values when set on the profile', () => {
    useAppStore.setState({
      authToken: 'tok',
      userProfile: { ...baseProfile, currentFTP: 280, maxHeartRate: 185, restingHeartRate: 55 },
    })
    render(<FitnessMetricsCard />)
    expect(screen.getByText('280')).toBeInTheDocument()
    expect(screen.getByText('185')).toBeInTheDocument()
    expect(screen.getByText('55')).toBeInTheDocument()
  })

  it('enters edit mode when Edit is clicked', async () => {
    useAppStore.setState({ authToken: 'tok', userProfile: baseProfile })
    render(<FitnessMetricsCard />)

    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    // Should show number inputs
    const inputs = screen.getAllByRole('spinbutton')
    expect(inputs.length).toBeGreaterThanOrEqual(4)
  })

  it('exits edit mode without saving when Cancel is clicked', async () => {
    useAppStore.setState({ authToken: 'tok', userProfile: { ...baseProfile, currentFTP: 250 } })
    render(<FitnessMetricsCard />)

    await userEvent.click(screen.getByRole('button', { name: /edit/i }))
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))

    // Should be back in view mode
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(mockUpdateCurrentUser).not.toHaveBeenCalled()
  })

  it('saves updated values and calls updateCurrentUser', async () => {
    useAppStore.setState({ authToken: 'tok-123', userProfile: baseProfile })
    render(<FitnessMetricsCard />)

    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    // Find FTP input (first spinbutton) and set a value
    const inputs = screen.getAllByRole('spinbutton')
    await userEvent.clear(inputs[0])
    await userEvent.type(inputs[0], '300')

    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith(
        'tok-123',
        expect.objectContaining({ currentFTP: 300 })
      )
    })
  })

  it('shows a validation error when a non-positive value is entered', async () => {
    useAppStore.setState({ authToken: 'tok', userProfile: baseProfile })
    render(<FitnessMetricsCard />)

    await userEvent.click(screen.getByRole('button', { name: /edit/i }))

    const inputs = screen.getAllByRole('spinbutton')
    await userEvent.clear(inputs[0])
    await userEvent.type(inputs[0], '-10')

    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    expect(screen.getByText(/all values must be positive numbers/i)).toBeInTheDocument()
    expect(mockUpdateCurrentUser).not.toHaveBeenCalled()
  })
})
