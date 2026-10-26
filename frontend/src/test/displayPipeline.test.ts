import { readFileSync, readdirSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// The coach's knowledge is stored in English, and what the athlete reads of it
// passes one display pipeline: `usePlanText`, which translates today and may
// filter later (ai-trainer-ops#258). The backend's half names what is owed
// (backend/tests/test_display_pipeline_258.py); this half finds every source
// file that touches the knowledge, so a new one cannot show it raw unnoticed.

const SRC = resolve(__dirname, '..')

/** A knowledge type, or a field only the knowledge carries. */
const KNOWLEDGE =
  /\b(AthleteHypothesis|AthleteOpenQuestion|AthleteExperiment|AthletePrediction|AthleteMemoryFact)\b|\.(statement|expectedOutcome|actualOutcome|alternativeExplanations|contradictionNote|horizon|protocol)\b/

/** Files that show the knowledge: through the pipeline, or handed the pipeline as `shown`. */
const SHOWS = new Set([
  'components/athleteModel/rows.tsx',
  'components/AthletePerformanceModelCard.tsx',
  'components/PredictionScoreboard.tsx',
  'utils/coachTimeline.ts',
])

/** Files that touch it without showing it, or show it for a reason. */
const EXEMPT: Record<string, string> = {
  'services/user.ts': 'the API client: types and requests',
  'components/AthleteTraitsSettings.tsx': 'groups the rows; the rows show the texts',
  'components/MotivationModelSettings.tsx':
    "the expert motivation model, edited in place; its texts are not the coach's knowledge rows, the next slice of ai-trainer-ops#258",
}

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sources(path)
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) && !entry.name.endsWith('.gen.ts')
      ? [path]
      : []
  })
}

const touching = sources(SRC)
  .filter((path) => KNOWLEDGE.test(readFileSync(path, 'utf8')))
  .map((path) => relative(SRC, path).split('\\').join('/'))

describe("the coach's knowledge reaches the screen through one pipeline (ai-trainer-ops#258)", () => {
  it('knows every file that touches it', () => {
    const unlisted = touching.filter((file) => !SHOWS.has(file) && !(file in EXEMPT))
    expect(unlisted, 'show it through usePlanText and list it in SHOWS, or list it in EXEMPT with the reason').toEqual([])
  })

  it.each([...SHOWS])('%s shows it through the pipeline', (file) => {
    const source = readFileSync(join(SRC, file), 'utf8')
    expect(source).toMatch(/usePlanText\(\)|shown: \(text: string\) => string/)
  })

  it('lists no file that is gone or no longer touches it', () => {
    const listed = [...SHOWS, ...Object.keys(EXEMPT)]
    expect(listed.filter((file) => !touching.includes(file))).toEqual([])
  })
})
