import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SetupGuidePage from './SetupGuidePage'

// A guide with what the real one does not have yet: an image from another host,
// and a heading with markup in it (ai-trainer-ops#305).
vi.mock('../guide/getting-started.md?raw', () => ({
  default: [
    '# Getting started',
    '![A tracking pixel](https://elsewhere.example/p.png)',
    '![Bundled](img/03-strava-import.png)',
    '## A **bold** step, with `code`',
  ].join('\n\n'),
}))

function renderGuide() {
  return render(
    <MemoryRouter>
      <SetupGuidePage language="en" />
    </MemoryRouter>,
  )
}

describe('the guide page with other content', () => {
  it('shows an image from another host as its words, and only the bundled one', () => {
    renderGuide()

    expect(screen.getByText('A tracking pixel')).toBeInTheDocument()
    const images = screen.getAllByRole('img')
    expect(images).toHaveLength(1)
    expect(images[0]).toHaveAttribute('alt', 'Bundled')
    expect(images[0].getAttribute('src')).toContain('03-strava-import')
  })

  it('gives a heading with markup the id of its words, as GitHub does', () => {
    renderGuide()

    expect(document.getElementById('a-bold-step-with-code')?.tagName).toBe('H2')
  })
})
