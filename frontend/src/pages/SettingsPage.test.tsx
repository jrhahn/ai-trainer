import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import SettingsPage from './SettingsPage'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../store/useAppStore'

const { mockUpdateCurrentUser, mockDeleteCurrentUser } = vi.hoisted(() => ({
  mockUpdateCurrentUser: vi.fn(),
  mockDeleteCurrentUser: vi.fn(),
}))

vi.mock('../services/user', () => ({
  updateCurrentUser: mockUpdateCurrentUser,
  deleteCurrentUser: mockDeleteCurrentUser,
}))

// StravaConnect uses strava services; stub the component
vi.mock('../components/StravaConnect', () => ({
  default: () => <div data-testid="strava-connect-stub" />,
}))

const baseProfile: UserProfile = {
  name: 'Alice',
  email: 'alice@example.com',
  bikeType: 'road',
  trainingGoal: 'ftp_improvement',
  followsTrainingPlan: true,
  fitnessLevel: 'intermediate',
}

function setup() {
  return render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  useAppStore.setState({
    authToken: 'tok-123',
    userProfile: baseProfile,
    aiProvider: 'openai',
  })
  vi.clearAllMocks()
  mockUpdateCurrentUser.mockResolvedValue({})
  mockDeleteCurrentUser.mockResolvedValue(undefined)
})

describe('SettingsPage', () => {
  it('renders the AI provider section', () => {
    setup()
    expect(screen.getByRole('heading', { name: /AI Provider/i })).toBeInTheDocument()
    expect(screen.getByText('OpenAI')).toBeInTheDocument()
    expect(screen.getByText('Google Gemini')).toBeInTheDocument()
  })

  it('displays the logged-in user email', () => {
    setup()
    expect(screen.getByText(/alice@example\.com/)).toBeInTheDocument()
  })

  it('saves the selected AI provider when Save is clicked', async () => {
    setup()

    await userEvent.click(screen.getByText('Google Gemini'))
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockUpdateCurrentUser).toHaveBeenCalledWith('tok-123', { aiProvider: 'gemini' })
    })
    expect(useAppStore.getState().aiProvider).toBe('gemini')
  })

  it('shows a success confirmation after saving', async () => {
    setup()
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(screen.getByText('AI settings saved!')).toBeInTheDocument()
    })
  })

  it('calls resetAll and deleteCurrentUser when the reset is confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    await waitFor(() => {
      expect(mockDeleteCurrentUser).toHaveBeenCalledWith('tok-123')
      expect(useAppStore.getState().authToken).toBeNull()
    })
  })

  it('does not call deleteCurrentUser when the reset dialog is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    setup()

    await userEvent.click(screen.getByRole('button', { name: /reset all data/i }))

    expect(mockDeleteCurrentUser).not.toHaveBeenCalled()
  })
})
