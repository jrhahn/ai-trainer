import type { ChatMessage } from '../../store/useAppStore'
import { useT } from '../../i18n'

// The app's own notes under a coach reply (ai-trainer-ops#262): what the app did
// around the reply, or found in it. They used to be appended to the coach's text
// in two styles; they sit beside it now, in one.
export default function ReplyNotes({ notes }: { notes: NonNullable<ChatMessage['notes']> }) {
  const t = useT()
  if (notes.length === 0) return null
  return (
    <ul className="mt-2 space-y-1 text-gray-600">
      {notes.map((note, index) => (
        <li key={`${note.kind}-${index}`}>
          <span className="font-medium text-gray-700">{t('Note')}:</span> {note.text}
        </li>
      ))}
    </ul>
  )
}
