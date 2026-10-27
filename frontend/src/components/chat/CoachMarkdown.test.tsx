import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import CoachMarkdown from './CoachMarkdown'
import { coachMarkdownSource } from '../coachMarkdown'

// The opening message's layout (ai-trainer-ops#308): a lead line, the three
// examples, then the assumptions and the pinned sentence with no blank line.
const OPENING = [
  'Hallo! Dein erster Plan steht.',
  'Schreib mir einfach, zum Beispiel:',
  '- „Montag passt mir besser als Dienstag“',
  '- „Am Wochenende habe ich mehr Zeit“',
  '- „Ich fahre am 12. Mai ein Rennen“',
  'Ich gehe erst mal davon aus, dass du 3 bis 6 Stunden pro Woche fährst.',
  'Die Fragen dazu sind oben im Chat angeheftet; antippen genügt.',
].join('\n')

describe('the list ends before the line after it (ai-trainer-ops#308)', () => {
  it('puts a blank line between the last item and the next line', () => {
    expect(coachMarkdownSource('- a\n- b\nAfter the list.')).toBe('- a\n- b\n\nAfter the list.')
  })

  it('ends a numbered list too', () => {
    expect(coachMarkdownSource('1. a\n2) b\nAfter.')).toBe('1. a\n2) b\n\nAfter.')
  })

  it('keeps an indented line in its item, and changes nothing else', () => {
    const text = 'Lead:\n- a\n  still a\n- b\n\nAfter a blank line.\n*not* a list item\n-5 W is none either\nso this line is no item’s'
    expect(coachMarkdownSource(text)).toBe(text)
  })

  it('starts a numbered list only where markdown starts one', () => {
    // A "2." after prose is prose in markdown: only 1 interrupts a paragraph.
    expect(coachMarkdownSource('Woche eins.\n2. Woche locker\nweiter')).toBe('Woche eins.\n2. Woche locker\nweiter')
    expect(coachMarkdownSource('Plan:\n1. eins\nweiter')).toBe('Plan:\n1. eins\n\nweiter')
    expect(coachMarkdownSource('Plan:\n\n3. drei\nweiter')).toBe('Plan:\n\n3. drei\n\nweiter')
  })

  it('keeps a German date at the start of a line from becoming a list number (review of #1175)', () => {
    expect(coachMarkdownSource('Ziel:\n26. April 2027 ist das Rennen\nBis dahin locker.')).toBe(
      'Ziel:\n26\\. April 2027 ist das Rennen\nBis dahin locker.',
    )
    expect(coachMarkdownSource('- 12. Mai: Rennen\n- 6. Sep. Ötztaler')).toBe('- 12\\. Mai: Rennen\n- 6\\. Sep. Ötztaler')
    expect(coachMarkdownSource('3. Woche: mehr\n12. Runde')).toBe('3. Woche: mehr\n12. Runde')
    // A word that only starts like a month stays a list item.
    const plan = '1. Juniorenplan\n2. Augenübungen\n3. Novemberplan\n4. Maifeld'
    expect(coachMarkdownSource(plan)).toBe(plan)
    expect(coachMarkdownSource('1. Juni 2027\n2. Sept. 2027')).toBe('1\\. Juni 2027\n2\\. Sept. 2027')
  })

  it('shows a date at the start of a message as a date, also inside a bullet', () => {
    const { container } = render(<CoachMarkdown text={'26. April 2027 ist das Rennen.\n\n- 12. Mai: Generalprobe'} />)

    expect(container.querySelector('ol')).toBeNull()
    expect(container.querySelector('p')?.textContent).toBe('26. April 2027 ist das Rennen.')
    expect(container.querySelector('li')?.textContent).toBe('12. Mai: Generalprobe')
  })

  it('reads "•" at the start of a line as a bullet', () => {
    expect(coachMarkdownSource('Zum Beispiel:\n• a\n  • b\nDanach.')).toBe('Zum Beispiel:\n- a\n  - b\n\nDanach.')
    // Inside a line it is text.
    expect(coachMarkdownSource('3 • 4 min\n•no space')).toBe('3 • 4 min\n•no space')

    const { container } = render(<CoachMarkdown text={'Zum Beispiel:\n• Ich habe am Samstag keine Zeit.\n• Ich bin heute müde.'} />)
    expect([...container.querySelectorAll('ul > li')].map((li) => li.textContent)).toEqual([
      'Ich habe am Samstag keine Zeit.',
      'Ich bin heute müde.',
    ])
  })

  it('leaves fenced code alone', () => {
    const text = '```\n- a\nb\n```\n- c\n\nd'
    expect(coachMarkdownSource(text)).toBe(text)
  })

  it('renders the sentence after a list below it, not inside its last item', () => {
    const { container } = render(<CoachMarkdown text={OPENING} />)

    const items = [...container.querySelectorAll('li')].map((li) => li.textContent)
    expect(items).toEqual([
      '„Montag passt mir besser als Dienstag“',
      '„Am Wochenende habe ich mehr Zeit“',
      '„Ich fahre am 12. Mai ein Rennen“',
    ])
    const after = container.querySelector('ul + p')
    expect(after?.textContent).toMatch(/^Ich gehe erst mal davon aus/)
    expect(after?.textContent).toMatch(/angeheftet; antippen genügt\.$/)
  })
})

describe('how a coach message looks (ai-trainer-ops#308)', () => {
  it('shows a list with bullets, and a numbered one with numbers', () => {
    const { container } = render(<CoachMarkdown text={'- a\n- b\n\n3. c\n4. d'} />)

    expect(container.querySelector('ul')).toHaveClass('list-disc', 'pl-5')
    expect(container.querySelector('ol')).toHaveClass('list-decimal', 'pl-5')
    expect(container.querySelector('ol')).toHaveAttribute('start', '3')
  })

  it('keeps space between paragraphs and lists, and a heading is a paragraph', () => {
    const { container } = render(<CoachMarkdown text={'One.\n\n## Two\nThree.\n- a'} />)

    const blocks = [...container.children]
    expect(blocks.map((block) => block.tagName)).toEqual(['P', 'P', 'P', 'UL'])
    for (const block of blocks) expect(block).toHaveClass('mt-2', 'first:mt-0')
  })

  it('breaks a line where the text breaks it, in a paragraph and in an item', () => {
    const { container } = render(
      <CoachMarkdown text={'Erste Zeile\nzweite **fett**\n*kursiv*\n\n- a\n  b'} />,
    )

    const paragraph = container.querySelector('p')!
    expect(paragraph.querySelectorAll('br')).toHaveLength(2)
    expect(paragraph.textContent).toBe('Erste Zeile\nzweite fett\nkursiv')
    expect(container.querySelector('li')!.querySelectorAll('br')).toHaveLength(1)
  })

  it('adds no break between the blocks of a list with blank lines', () => {
    const { container } = render(<CoachMarkdown text={'- one\n\n- two\n\n- three'} />)

    expect(container.querySelectorAll('li p')).toHaveLength(3)
    expect(container.querySelector('br')).toBeNull()
  })

  it('leaves code as written', () => {
    const { container } = render(<CoachMarkdown text={'`a\nb`\n\n```\nx\ny\n```'} />)

    expect(container.querySelector('br')).toBeNull()
    expect(container.querySelector('pre code')?.textContent).toBe('x\ny\n')
  })
})
