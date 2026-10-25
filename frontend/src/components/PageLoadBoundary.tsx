import { Component, type ReactNode } from 'react'
import { useT } from '../i18n'
import { reloadingForChunk } from '../lib/chunkReload'
import { button, card, cardTitle } from './ui'

function PageLoadFailed({ onRetry }: { onRetry: () => void }) {
  const t = useT()
  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div role="alert" className={`${card} max-w-md w-full`}>
        <h1 className={cardTitle}>{t('This page could not be loaded')}</h1>
        <p className="mt-2 text-sm text-gray-600">{t('Nothing was changed. Try again, and if it happens again, check the connection.')}</p>
        <button type="button" onClick={onRetry} className={`${button} mt-5 min-h-10`}>
          {t('Try again')}
        </button>
      </div>
    </main>
  )
}

/** A page whose code did not arrive (ai-trainer-ops#275).
 *
 * The pages load when they are opened. On a dropped connection, or when the
 * reload after a deploy (`lib/chunkReload.ts`) did not help, a page's code
 * fails to load, and without this the whole app went blank. It catches any
 * error a page throws, not only that one: a blank app is worse, and the text
 * holds for both (review of #1071). */
export default class PageLoadBoundary extends Component<
  { children: ReactNode; reload?: () => void },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    if (reloadingForChunk()) return null
    return <PageLoadFailed onRetry={this.props.reload ?? (() => window.location.reload())} />
  }
}
