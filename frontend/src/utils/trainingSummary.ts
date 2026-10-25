/*
 * The coach's training summary, stored as JSON or as text with "- " bullets,
 * split into an intro and labelled bullets. Out of `DashboardPage` (ai-trainer-ops#274).
 */

export function splitTrainingSummary(raw: string): {
  intro: string
  bullets: { label?: string; text: string }[]
} {
  let intro = ''
  let rawBullets: string[] = []

  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      const summary = parsed as Record<string, unknown>
      const parsedBullets = Array.isArray(summary.bulletPoints)
        ? summary.bulletPoints
        : Array.isArray(summary.bullets)
          ? summary.bullets
          : null

      if ('intro' in summary && parsedBullets) {
        intro = String(summary.intro)
        rawBullets = parsedBullets.map(String)
      } else {
        rawBullets = Object.values(summary).map(String)
      }
    }
  } catch {
    const lines = raw.split('\n')
    const introLines: string[] = []
    for (const line of lines) {
      if (line.startsWith('- ')) {
        rawBullets.push(line)
      } else {
        introLines.push(line)
      }
    }
    intro = introLines.join(' ').trim()
  }

  const bullets = rawBullets.map((b) => {
    const normalized = b.replace(/\n+\s*/g, ' ').trim()
    const withoutDash = normalized.startsWith('- ') ? normalized.slice(2) : normalized
    const decoded = withoutDash
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
    const colonIdx = decoded.indexOf(': ')
    if (colonIdx > 0) {
      return { label: decoded.slice(0, colonIdx), text: decoded.slice(colonIdx + 2) }
    }
    return { text: decoded }
  })

  return { intro, bullets }
}
