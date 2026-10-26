import ReactMarkdown from 'react-markdown'
import { MARKDOWN_COMPONENTS, coachMarkdownSource, remarkLineBreaks } from '../coachMarkdown'

const REMARK_PLUGINS = [remarkLineBreaks]

/** A coach message's text as markdown: paragraphs, lists and line breaks as
 *  written, and nothing that reaches another host (coachMarkdown.tsx). */
export default function CoachMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown components={MARKDOWN_COMPONENTS} remarkPlugins={REMARK_PLUGINS}>
      {coachMarkdownSource(text)}
    </ReactMarkdown>
  )
}
