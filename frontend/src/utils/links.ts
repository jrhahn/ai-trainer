import type { Language } from '../i18n'

export const GITHUB_URL = 'https://github.com/jrhahn/ai-trainer'

const GUIDE_BASE = `${GITHUB_URL}/blob/develop/docs`

// The guide exists in both languages (docs/getting-started.md and
// docs/erste-schritte.md), so a German interface opens the German one
// (ai-trainer-ops#131). The anchors are GitHub's slugs of each file's headings.
const GUIDES = {
  en: {
    url: `${GUIDE_BASE}/getting-started.md`,
    intervals: '#2-connect-intervalsicu-to-train-like-a-pro',
    geminiKey: '#3-add-your-google-gemini-key',
  },
  de: {
    url: `${GUIDE_BASE}/erste-schritte.md`,
    intervals: '#2-intervalsicu-mit-train-like-a-pro-verbinden',
    geminiKey: '#3-google-gemini-key-hinterlegen',
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
