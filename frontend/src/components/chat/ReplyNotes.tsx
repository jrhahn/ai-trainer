import type { ChatMessage } from '../../store/useAppStore'
import { useT } from '../../i18n'

// The app's own notes under a coach reply (ai-trainer-ops#262): what the app did
// around the reply, or found in it. They used to be appended to the coach's text
// in two styles; they sit beside it now, in one, under one heading
// (review of 2026-11-01, ai-trainer-ops#338 A10).
export default function ReplyNotes({ notes }: { notes: NonNullable<ChatMessage['notes']> }) {
  const t = useT()
  if (notes.length === 0) return null
  return (
    <div>
      <p className="font-medium text-gray-700">{t('From the app')}</p>
      <ul className="mt-1 space-y-1 text-gray-600">
        {notes.map((note, index) => (
          <li key={`${note.kind}-${index}`}>{note.text}</li>
        ))}
      </ul>
    </div>
  )
}
