import type { Language } from '../i18n'

export const GITHUB_URL = 'https://github.com/jrhahn/ai-trainer'

// The guide is a page of the app in both languages, /anleitung and /guide
// (ai-trainer-ops#305), so a German interface opens the German one
// (ai-trainer-ops#131). It is the testers' guide with its screenshots
// (ai-trainer-ops#219); the intervals.icu link opens it at the device step,
// where the rides start. It opened on GitHub before; the anchors are the ones
// GitHub gave its headings, and `headingId` makes the same.
const GUIDES = {
  en: {
    url: '/guide',
    intervals: '#3-connect-your-device-to-intervalsicu',
    geminiKey: '#optional-your-own-google-gemini-key',
  },
  de: {
    url: '/anleitung',
    intervals: '#3-dein-gerät-mit-intervalsicu-verbinden',
    geminiKey: '#optional-eigener-google-gemini-key',
  },
} as const

/** A heading's anchor, as GitHub makes it: lowercase, punctuation gone, spaces to hyphens. */
export function headingId(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N} _-]/gu, '')
    .replace(/ /g, '-')
}

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
