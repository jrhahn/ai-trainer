import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RaceOffer from './RaceOffer'
import AddRaceDialog from './AddRaceDialog'
import { useAppStore } from '../store/useAppStore'

const { mockCreate, mockUpdateUser, mockUpdateEvent } = vi.hoisted(() => ({
  mockCreate: vi.fn(),
  mockUpdateUser: vi.fn(),
  mockUpdateEvent: vi.fn(),
}))
vi.mock('../services/user', () => ({
  createRaceEvent: mockCreate,
  updateCurrentUser: mockUpdateUser,
  updateRaceEventRemote: mockUpdateEvent,
}))

const created = { id: 'r1', date: '2026-11-11', startTime: null, distanceKm: 120, elevationM: 1800 }

beforeEach(() => {
  useAppStore.getState().resetAll()
  useAppStore.setState({ authToken: 'tok' })
  vi.clearAllMocks()
  mockCreate.mockResolvedValue(created)
})

async function fillCourse(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Distance/), '120')
  await user.type(screen.getByLabelText(/Climb/), '1800')
}

describe('RaceOffer (ai-trainer-ops#175)', () => {
  it('shows the date the server read and saves nothing before Add', () => {
    render(<RaceOffer date="2026-11-11" />)

    expect(screen.getByText(/Add a race on Wed,? (11 Nov|Nov 11),? 2026 to your calendar\?/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('Add opens the race form with that date, and saving puts it in the calendar', async () => {
    const user = userEvent.setup()
    render(<RaceOffer date="2026-11-11" />)

    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getByLabelText('Date')).toHaveValue('2026-11-11')
    await fillCourse(user)
    await user.click(screen.getByRole('button', { name: 'Add race' }))

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith('tok', {
        date: '2026-11-11',
        startTime: null,
        distanceKm: 120,
        elevationM: 1800,
      }),
    )
    expect(useAppStore.getState().raceEvents).toEqual([created])
    expect(await screen.findByText(/Added\. The dashboard now offers to rebuild your plan/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('the athlete can correct the date before saving', async () => {
    const user = userEvent.setup()
    render(<RaceOffer date="2026-11-11" />)

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.clear(screen.getByLabelText('Date'))
    await user.type(screen.getByLabelText('Date'), '2026-11-14')
    await fillCourse(user)
    await user.click(screen.getByRole('button', { name: 'Add race' }))

    await waitFor(() => expect(mockCreate.mock.calls[0][1].date).toBe('2026-11-14'))
  })

  it('closing the form keeps the offer, No thanks removes it', async () => {
    const user = userEvent.setup()
    const { container } = render(<RaceOffer date="2026-11-11" />)

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.click(screen.getByRole('button', { name: 'Close dialog' }))
    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'No thanks' }))
    expect(container).toBeEmptyDOMElement()
  })
})

describe('RaceOffer for a moved race (ai-trainer-ops#175, comment 5111)', () => {
  it('moves the profile race instead of adding a second one', async () => {
    mockUpdateUser.mockResolvedValue({ profile: { name: 'Lena', raceDate: '2027-01-17' } })
    const user = userEvent.setup()
    render(<RaceOffer date="2027-01-17" replaces={{ date: '2026-12-30', kind: 'profile' }} />)

    expect(screen.getByText(/Move your race from .*30.* to .*17.*\?/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Move' }))

    expect(mockUpdateUser).toHaveBeenCalledWith('tok', { raceDate: '2027-01-17' })
    expect(mockCreate).not.toHaveBeenCalled()
    expect(useAppStore.getState().userProfile?.raceDate).toBe('2027-01-17')
    expect(await screen.findByText(/Moved\. The dashboard now offers to rebuild your plan/)).toBeInTheDocument()
  })

  it('moves the calendar race and keeps its course', async () => {
    const race = { id: 'r9', date: '2026-12-30', startTime: '09:00', distanceKm: 14, elevationM: 850 }
    useAppStore.setState({ raceEvents: [race] })
    mockUpdateEvent.mockResolvedValue({ ...race, date: '2027-01-17' })
    const user = userEvent.setup()
    render(<RaceOffer date="2027-01-17" replaces={{ date: '2026-12-30', kind: 'calendar', eventId: 'r9' }} />)

    await user.click(screen.getByRole('button', { name: 'Move' }))

    expect(mockUpdateEvent).toHaveBeenCalledWith('tok', 'r9', {
      date: '2027-01-17',
      startTime: '09:00',
      distanceKm: 14,
      elevationM: 850,
    })
    await waitFor(() => expect(useAppStore.getState().raceEvents[0].date).toBe('2027-01-17'))
  })

  it('says so when the move fails, and keeps the offer', async () => {
    mockUpdateUser.mockRejectedValue(new Error('offline'))
    const user = userEvent.setup()
    render(<RaceOffer date="2027-01-17" replaces={{ date: '2026-12-30', kind: 'profile' }} />)

    await user.click(screen.getByRole('button', { name: 'Move' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not move the race.')
    expect(screen.getByRole('button', { name: 'Move' })).toBeEnabled()
  })
})

describe('AddRaceDialog (ai-trainer-ops#175)', () => {
  it('needs a date, a distance and the climbing', async () => {
    const user = userEvent.setup()
    render(<AddRaceDialog onClose={() => {}} />)
    const save = screen.getByRole('button', { name: 'Add race' })

    expect(save).toBeDisabled()
    await fillCourse(user)
    expect(save).toBeDisabled()
    await user.type(screen.getByLabelText('Date'), '2027-05-01')
    expect(save).toBeEnabled()
  })

  it('keeps the form and says so when saving fails', async () => {
    mockCreate.mockRejectedValue(new Error('Could not save race event'))
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<AddRaceDialog date="2027-05-01" onClose={onClose} />)

    await fillCourse(user)
    await user.click(screen.getByRole('button', { name: 'Add race' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save race event')
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Add race' })).toBeEnabled()
  })

  it('a failure that is not an Error still says something', async () => {
    mockCreate.mockRejectedValue('boom')
    const user = userEvent.setup()
    render(<AddRaceDialog date="2027-05-01" onClose={() => {}} />)

    await fillCourse(user)
    await user.type(screen.getByLabelText('Time'), '09:30')
    await user.click(screen.getByRole('button', { name: 'Add race' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save race event')
    expect(mockCreate.mock.calls[0][1].startTime).toBe('09:30')
  })
})
