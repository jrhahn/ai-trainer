/** After a deploy, load the new version instead of a blank page (ai-trainer-ops#275).

The pages come in chunks named by their content. A deploy replaces them, so a
page opened before it asks for a chunk that is gone, and Vite reports
`vite:preloadError`. The page then loads once more, which brings the new
`index.html` and its chunks. A second failure soon after that one is not a
deploy (a dropped connection, say): it is left to `PageLoadBoundary` instead of
reloading for ever. */

const RELOADED_KEY = 'ai_trainer_reloaded_for_chunk'
const RELOAD_WINDOW_MS = 10_000

let reloading = false

/** Whether this page is on its way to the new version. The failed import still
 *  reaches `PageLoadBoundary`, which then shows nothing for that moment rather
 *  than an error (review of #1071). */
export function reloadingForChunk(): boolean {
  return reloading
}

export function reloadOnceForMissingChunk(event: Event, reload: () => void = () => window.location.reload()): void {
  try {
    if (Date.now() - Number(sessionStorage.getItem(RELOADED_KEY)) < RELOAD_WINDOW_MS) return
    sessionStorage.setItem(RELOADED_KEY, String(Date.now()))
  } catch {
    // No storage, so nothing would stop a reload loop: `PageLoadBoundary` offers it instead.
    return
  }
  reloading = true
  event.preventDefault()
  reload()
}
