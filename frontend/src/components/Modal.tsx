import { useRef } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { useOverlayDismiss } from '../hooks/useOverlayDismiss'

/** A centred overlay panel, for detail the dashboard should not carry inline. */
export default function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  useOverlayDismiss(open, onClose, panelRef)

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-3 sm:p-6" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="relative flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
          <h2 className="text-sm font-bold text-gray-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
          >
            <X size={18} />
          </button>
        </div>
        {/* The calendar is taller than a phone; scroll it inside the panel so the
            page behind never scrolls with it. */}
        <div className="overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  )
}
