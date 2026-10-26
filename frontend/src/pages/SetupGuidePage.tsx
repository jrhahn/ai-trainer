import { Children, isValidElement, useEffect, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import ReactMarkdown, { type Components } from 'react-markdown'
import { ArrowLeft } from 'lucide-react'
import { useT, type Language } from '../i18n'
import { headingId } from '../utils/links'
import ersteSchritte from '../guide/erste-schritte.md?raw'
import gettingStarted from '../guide/getting-started.md?raw'

const GUIDE: Record<Language, string> = { de: ersteSchritte, en: gettingStarted }

// The screenshots by the path the guide gives them ("img/01-….png"), bundled
// with the app. Any other image renders as its words: the guide shows nothing
// from another host.
const IMAGES: Record<string, string> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>('../guide/img/*.png', { eager: true, query: '?url', import: 'default' }),
  ).map(([path, url]) => [`img/${path.split('/').pop()}`, url]),
)

/** A heading's text, for its id. */
function textOf(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) =>
      typeof child === 'string' || typeof child === 'number'
        ? String(child)
        : isValidElement<{ children?: ReactNode }>(child)
          ? textOf(child.props.children)
          : '',
    )
    .join('')
}

const link = 'font-medium text-amber-700 underline hover:text-amber-800'

// The privacy page's style: the same headings, text and width (ai-trainer-ops#305).
const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 id={headingId(textOf(children))} className="mt-6 text-2xl font-semibold text-gray-900">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 id={headingId(textOf(children))} className="mt-10 scroll-mt-6 text-lg font-semibold text-gray-900">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 id={headingId(textOf(children))} className="mt-6 scroll-mt-6 font-semibold text-gray-900">
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="mt-3 text-sm leading-6 text-gray-700">{children}</p>,
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm leading-6 text-gray-700 marker:text-gray-500">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm leading-6 text-gray-700 marker:text-gray-500">{children}</ol>
  ),
  strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
  hr: () => <hr className="mt-10 border-gray-200" />,
  // The one code block is the device → intervals.icu → app line: on a phone it
  // wraps, rather than widening the page or hiding its end.
  pre: ({ children }) => (
    <pre className="mt-4 whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-2 text-xs text-gray-700">{children}</pre>
  ),
  img: ({ src, alt }) => {
    const bundled = typeof src === 'string' ? IMAGES[src] : undefined
    return bundled ? (
      <img src={bundled} alt={alt ?? ''} loading="lazy" className="mt-1 w-full rounded-lg border border-gray-200" />
    ) : (
      <span className="italic text-gray-500">{alt}</span>
    )
  },
  // The other language is a page of the app; everything else opens beside it.
  a: ({ href, children }) =>
    href?.startsWith('/') ? (
      <Link to={href} className={link}>
        {children}
      </Link>
    ) : (
      <a href={href} target="_blank" rel="noreferrer" className={link}>
        {children}
      </a>
    ),
}

/**
 * The setup guide as a page of the app (ai-trainer-ops#305): /anleitung in
 * German, /guide in English, so a link carries its language. It opened on
 * GitHub before, with the repository's menus around it.
 *
 * The text is the two markdown files in src/guide, with their screenshots.
 * Each heading's id is GitHub's anchor for it, so the links into a section
 * (`setupGuideSection`) kept working when the guide moved. Reachable signed in
 * and signed out: the landing page and the invitation link here.
 */
export default function SetupGuidePage({ language }: { language: Language }) {
  const t = useT()
  const { hash } = useLocation()

  // The page renders after its route, so the browser finds no section to jump
  // to by itself. Without one it opens at the top: the link to the other
  // language sits at the end, and the router keeps the scroll position (review
  // of #1176).
  useEffect(() => {
    if (!hash) {
      window.scrollTo(0, 0)
      return
    }
    try {
      document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView()
    } catch {
      // A malformed address: the page opens where it is.
    }
  }, [hash, language])

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft size={16} aria-hidden="true" /> {t('Back')}
        </Link>
        <article lang={language}>
          <ReactMarkdown components={COMPONENTS}>{GUIDE[language]}</ReactMarkdown>
        </article>
      </div>
    </main>
  )
}
