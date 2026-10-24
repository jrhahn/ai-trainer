import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { setupGuideSection, setupGuideUrl } from './links'

/** GitHub's anchor for each heading of a doc: lowercase, punctuation gone, spaces to hyphens. */
function anchors(file: string): string[] {
  // From the frontend directory, where vitest runs.
  const text = readFileSync(`${process.cwd()}/../docs/${file}`, 'utf8')
  return [...text.matchAll(/^#+ (.+)$/gm)].map(
    (m) => '#' + m[1].toLowerCase().replace(/[^\p{L}\p{N} -]/gu, '').replace(/ /g, '-'),
  )
}

// The guide opens in the interface's language (ai-trainer-ops#131).
describe('setup guide links', () => {
  it('opens the German guide in a German interface', () => {
    expect(setupGuideUrl('de')).toMatch(/docs\/erste-schritte\.md$/)
    expect(setupGuideSection('de', 'intervals')).toMatch(/erste-schritte\.md#3-dein-gerät-mit-intervalsicu-verbinden$/)
    expect(setupGuideSection('de', 'geminiKey')).toMatch(/erste-schritte\.md#optional-eigener-google-gemini-key$/)
  })

  it('opens the English guide in an English interface, and when the language is unknown', () => {
    expect(setupGuideUrl('en')).toMatch(/docs\/getting-started\.md$/)
    expect(setupGuideSection('en', 'geminiKey')).toMatch(/getting-started\.md#optional-your-own-google-gemini-key$/)
    expect(setupGuideUrl(undefined)).toBe(setupGuideUrl('en'))
  })
})

// The headings are the anchors, so a reworded heading must take its link along
// (ai-trainer-ops#219).
describe('every guide link lands on a heading', () => {
  it.each([
    ['de', 'erste-schritte.md'],
    ['en', 'getting-started.md'],
  ] as const)('%s', (language, file) => {
    for (const section of ['intervals', 'geminiKey'] as const) {
      const anchor = setupGuideSection(language, section).split('.md')[1]
      expect(anchors(file)).toContain(anchor)
    }
  })
})
