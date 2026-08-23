import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import LandingPage from './LandingPage'

function setup() {
  render(
    <MemoryRouter>
      <LandingPage />
    </MemoryRouter>
  )
}

describe('LandingPage', () => {
  it('leads with the coaching promise rather than metrics', () => {
    setup()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/coach that\s*actually knows you/i)
  })

  it('offers both sign-up and sign-in paths', () => {
    setup()
    expect(screen.getAllByRole('link', { name: /start training free/i }).length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: /^sign in$/i })).toHaveAttribute('href', '/login')
    expect(screen.getByRole('link', { name: /get started/i })).toHaveAttribute('href', '/register')
  })

  it('names the differentiators: free, open source, own key, Strava via intervals.icu', () => {
    setup()
    expect(screen.getByText(/free forever · open source/i)).toBeInTheDocument()
    expect(screen.getByText(/free google gemini key/i)).toBeInTheDocument()
    expect(screen.getAllByText(/agpl-3\.0/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/intervals\.icu once and your strava rides flow in/i)).toBeInTheDocument()
  })

  it('points new users at the setup guide', () => {
    setup()
    const guideLinks = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.includes('docs/getting-started.md'))
    expect(guideLinks.length).toBeGreaterThan(0)
  })

  it('promises nutrition questions too', () => {
    setup()
    expect(screen.getByText(/training and nutrition questions welcome/i)).toBeInTheDocument()
  })
})
