// Summarise a flake census: Playwright JSON reports from runs with
// `--repeat-each`, tallied per spec, test and viewport (ai-trainer-ops#291).
//
//   node scripts/e2e-census.mjs census-keyless.json census-stubbed.json
//
// Prints a Markdown table of every test that failed at least once, and exits 1
// when there is one, so the nightly run is red until each is fixed or filed.
import { readFileSync } from 'node:fs'

/** Every test run in a report, one entry per repeat and viewport. */
function* runs(suite, file) {
  const here = suite.file ?? file
  for (const spec of suite.specs ?? []) {
    for (const test of spec.tests ?? []) {
      yield {
        key: `${here} › ${spec.title} [${test.projectName || 'default'}]`,
        failed: test.status === 'unexpected' || test.status === 'flaky',
        skipped: test.status === 'skipped',
      }
    }
  }
  for (const child of suite.suites ?? []) yield* runs(child, here)
}

/** Tally the reports: how often each test ran and how often it failed. */
export function tally(reports) {
  const counts = new Map()
  for (const [stack, report] of reports) {
    for (const suite of report.suites ?? []) {
      for (const run of runs(suite, suite.title)) {
        if (run.skipped) continue
        const key = `${stack}: ${run.key}`
        const entry = counts.get(key) ?? { runs: 0, failures: 0 }
        entry.runs += 1
        if (run.failed) entry.failures += 1
        counts.set(key, entry)
      }
    }
  }
  return counts
}

export function summary(counts) {
  const failing = [...counts].filter(([, c]) => c.failures > 0).sort(([a], [b]) => a.localeCompare(b))
  const total = [...counts.values()].reduce((sum, c) => sum + c.runs, 0)
  const lines = ['## E2E flake census', '', `${counts.size} tests, ${total} runs.`, '']
  if (failing.length === 0) {
    lines.push('Every test passed in every run.')
  } else {
    lines.push(`${failing.length} failed at least once:`, '', '| Test | Failed |', '| --- | --- |')
    for (const [key, c] of failing) lines.push(`| ${key.replaceAll('|', '\\|')} | ${c.failures} of ${c.runs} |`)
  }
  return { text: lines.join('\n') + '\n', failing: failing.length }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const reports = process.argv.slice(2).map((path) => {
    const stack = path.replace(/^.*census-|\.json$/g, '')
    return [stack, JSON.parse(readFileSync(path, 'utf8'))]
  })
  const { text, failing } = summary(tally(reports))
  process.stdout.write(text)
  process.exit(failing > 0 ? 1 : 0)
}
