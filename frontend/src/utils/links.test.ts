import { describe, expect, it } from 'vitest'
import { setupGuideSection, setupGuideUrl } from './links'

// The guide opens in the interface's language (ai-trainer-ops#131).
describe('setup guide links', () => {
  it('opens the German guide in a German interface', () => {
    expect(setupGuideUrl('de')).toMatch(/docs\/erste-schritte\.md$/)
    expect(setupGuideSection('de', 'intervals')).toMatch(/erste-schritte\.md#2-intervalsicu-mit-train-like-a-pro-verbinden$/)
    expect(setupGuideSection('de', 'geminiKey')).toMatch(/erste-schritte\.md#3-google-gemini-key-hinterlegen$/)
  })

  it('opens the English guide in an English interface, and when the language is unknown', () => {
    expect(setupGuideUrl('en')).toMatch(/docs\/getting-started\.md$/)
    expect(setupGuideSection('en', 'geminiKey')).toMatch(/getting-started\.md#3-add-your-google-gemini-key$/)
    expect(setupGuideUrl(undefined)).toBe(setupGuideUrl('en'))
  })
})
