import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ThumbsDown, Trash2 } from 'lucide-react'
import { useT } from '../../i18n'

/** One of a row's answers: what it calls, the words if it fails, and its button's title and label. */
export interface RowAnswer {
  run: () => Promise<unknown>
  /** Shown, translated, when the call fails without a message of its own. */
  failure: string
  title: string
  label: string
}

/**
 * A row the athlete answers: a hypothesis, an open question, an experiment or a
 * prediction (ai-trainer-ops#274: four copies of it became this). Yes, no and
 * delete, which asks first. Each refreshes the lists in `refresh`, and a failure
 * says why under the row.
 */
export default function ReviewableRow({
  refresh,
  yes,
  no,
  remove,
  children,
}: {
  refresh: readonly string[]
  yes: RowAnswer
  no: RowAnswer
  remove: RowAnswer & { confirm: string }
  children: ReactNode
}) {
  const queryClient = useQueryClient()
  const t = useT()
  const [error, setError] = useState('')

  const answering = (answer: RowAnswer) => ({
    mutationFn: answer.run,
    onSuccess: () => {
      for (const key of refresh) void queryClient.invalidateQueries({ queryKey: [key] })
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : answer.failure),
  })
  const yesMutation = useMutation(answering(yes))
  const noMutation = useMutation(answering(no))
  const removeMutation = useMutation(answering(remove))
  const isBusy = yesMutation.isPending || noMutation.isPending || removeMutation.isPending

  const handleDelete = () => {
    if (typeof window !== 'undefined' && !window.confirm(remove.confirm)) return
    removeMutation.mutate()
  }

  return (
    <li className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">{children}</div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => yesMutation.mutate()}
            disabled={isBusy}
            title={yes.title}
            aria-label={yes.label}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <CheckCircle2 size={15} />
          </button>
          <button
            onClick={() => noMutation.mutate()}
            disabled={isBusy}
            title={no.title}
            aria-label={no.label}
            className="p-1.5 text-gray-400 hover:text-gray-900 disabled:opacity-50"
          >
            <ThumbsDown size={15} />
          </button>
          <button
            onClick={handleDelete}
            disabled={isBusy}
            title={remove.title}
            aria-label={remove.label}
            className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {error && <p className="text-sm text-red-600 mt-1.5">{t(error)}</p>}
    </li>
  )
}
