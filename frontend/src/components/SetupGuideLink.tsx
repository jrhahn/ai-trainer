export default function SetupGuideLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-xs font-medium text-amber-600 hover:text-amber-700 hover:underline"
    >
      {label}
    </a>
  )
}
