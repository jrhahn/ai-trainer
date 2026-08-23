import { HelpCircle } from 'lucide-react'

export default function SetupGuideLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 hover:text-amber-700"
    >
      <HelpCircle size={13} aria-hidden="true" />
      {label}
    </a>
  )
}
