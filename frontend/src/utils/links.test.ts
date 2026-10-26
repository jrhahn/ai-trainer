import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { headingId, setupGuideSection, setupGuideUrl } from './links'

/** The id the guide page gives each heading of a guide file. */
function anchors(file: string): string[] {
  // From the frontend directory, where vitest runs.
  const text = readFileSync(`${process.cwd()}/src/guide/${file}`, 'utf8')
  return [...text.matchAll(/^#+ (.+)$/gm)].map((m) => '#' + headingId(m[1]))
}

// The guide opens in the interface's language (ai-trainer-ops#131), as a page
// of the app (ai-trainer-ops#305).
describe('setup guide links', () => {
  it('opens the German guide in a German interface', () => {
    expect(setupGuideUrl('de')).toBe('/anleitung')
    expect(setupGuideSection('de', 'intervals')).toBe('/anleitung#3-dein-gerät-mit-intervalsicu-verbinden')
    expect(setupGuideSection('de', 'geminiKey')).toBe('/anleitung#optional-eigener-google-gemini-key')
  })

  it('opens the English guide in an English interface, and when the language is unknown', () => {
    expect(setupGuideUrl('en')).toBe('/guide')
    expect(setupGuideSection('en', 'intervals')).toBe('/guide#3-connect-your-device-to-intervalsicu')
    expect(setupGuideSection('en', 'geminiKey')).toBe('/guide#optional-your-own-google-gemini-key')
    expect(setupGuideUrl(undefined)).toBe(setupGuideUrl('en'))
  })
})

// The anchors GitHub gave the headings, which links in the wild still carry.
describe('headingId', () => {
  it.each([
    ['3. Dein Gerät mit intervals.icu verbinden', '3-dein-gerät-mit-intervalsicu-verbinden'],
    ['Optional: your own Google Gemini key', 'optional-your-own-google-gemini-key'],
    ['Optional: eigener Google-Gemini-Key', 'optional-eigener-google-gemini-key'],
    ['Something not working?', 'something-not-working'],
  ])('%s', (heading, id) => {
    expect(headingId(heading)).toBe(id)
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
      const anchor = setupGuideSection(language, section).slice(setupGuideUrl(language).length)
      expect(anchors(file)).toContain(anchor)
    }
  })
})
