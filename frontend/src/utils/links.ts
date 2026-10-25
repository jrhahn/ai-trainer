import type { Language } from '../i18n'

export const GITHUB_URL = 'https://github.com/jrhahn/ai-trainer'

const GUIDE_BASE = `${GITHUB_URL}/blob/develop/docs`

// The guide exists in both languages (docs/getting-started.md and
// docs/erste-schritte.md), so a German interface opens the German one
// (ai-trainer-ops#131). The anchors are GitHub's slugs of each file's headings.
// It is the testers' guide with its screenshots (ai-trainer-ops#219); the
// intervals.icu link opens it at the device step, where the rides start.
const GUIDES = {
  en: {
    url: `${GUIDE_BASE}/getting-started.md`,
    intervals: '#3-connect-your-device-to-intervalsicu',
    geminiKey: '#optional-your-own-google-gemini-key',
  },
  de: {
    url: `${GUIDE_BASE}/erste-schritte.md`,
    intervals: '#3-dein-gerät-mit-intervalsicu-verbinden',
    geminiKey: '#optional-eigener-google-gemini-key',
  },
} as const

// English for any language without a guide of its own.
function guideFor(language: Language | undefined) {
  return language && language in GUIDES ? GUIDES[language] : GUIDES.en
}

export function setupGuideUrl(language: Language | undefined): string {
  return guideFor(language).url
}

export function setupGuideSection(language: Language | undefined, section: 'intervals' | 'geminiKey'): string {
  const guide = guideFor(language)
  return `${guide.url}${guide[section]}`
}

/** The link a friend opens: the registration form with the code filled in. */
export function inviteLink(code: string): string {
  return `${window.location.origin}/register?invite=${encodeURIComponent(code)}`
}
