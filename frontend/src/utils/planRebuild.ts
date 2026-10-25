// What the rebuild notice says and remembers (ai-trainer-ops#206), out of
// `PlanRebuildNotice` so its tests can read it while a component file exports
// only components (fast refresh, ai-trainer-ops#286).

/** How each reason the backend names reads to the athlete. */
export const REASON_TEXT: Record<string, string> = {
  training_days: 'your training days changed',
  sports: 'your sports changed',
  level: 'your level changed',
  ftp: 'your FTP changed',
  history: 'there are new rides on record',
  race: 'your race changed',
  assumptions: 'you answered my questions about you',
  hours: 'the hours you can train changed',
}

/** A rebuild older than this is old news on the dashboard. */
export const NOTICE_MAX_AGE_MS = 3 * 24 * 60 * 60 * 1000

/** Where "seen" is kept: per browser and account, by the rebuild's time. */
export const seenKey = (account: string) => `plan-rebuild-seen:${account}`
