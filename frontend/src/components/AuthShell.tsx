import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Bike, KeyRound, MessageCircle, RefreshCw } from 'lucide-react'
import CoachChatPreview from './CoachChatPreview'

type AuthShellProps = {
  children: ReactNode
  eyebrow: string
  title: string
  subtitle: string
}

const highlights = [
  { label: 'A coach that remembers your goals', icon: MessageCircle },
  { label: 'Rides sync themselves from Strava', icon: RefreshCw },
  { label: 'Free — bring your own Gemini key', icon: KeyRound },
]

export default function AuthShell({ children, eyebrow, title, subtitle }: AuthShellProps) {
  return (
    <main className="min-h-screen bg-[#0f1116] text-white">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col justify-center gap-8 px-5 py-8 sm:px-8 lg:grid lg:grid-cols-[minmax(0,1.05fr)_minmax(380px,0.8fr)] lg:items-center lg:gap-12">
        <section className="relative overflow-hidden rounded-2xl border border-white/10 bg-[#12141b] p-6 shadow-2xl sm:p-8">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(40rem_24rem_at_10%_-10%,rgba(245,158,11,0.2),transparent_60%),radial-gradient(32rem_22rem_at_95%_20%,rgba(20,184,166,0.14),transparent_62%)]" />
          <div className="relative">
            <Link to="/" className="mb-10 inline-flex items-center gap-2">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500 text-[#0f1116]">
                <Bike size={20} aria-hidden="true" />
              </span>
              <span className="text-lg font-bold">Train Like a Pro</span>
            </Link>

            <p className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">{eyebrow}</p>
            <h1 className="max-w-xl text-4xl font-black leading-[1.1] tracking-tight sm:text-5xl">{title}</h1>
            <p className="mt-5 max-w-xl text-base leading-7 text-slate-300">{subtitle}</p>

            <div className="mt-8 hidden lg:block">
              <CoachChatPreview />
            </div>

            <div className="mt-8 grid gap-2 sm:grid-cols-3 lg:hidden">
              {highlights.map(({ label, icon: Icon }) => (
                <div
                  key={label}
                  className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-300">
                    <Icon size={16} aria-hidden="true" />
                  </span>
                  <span className="text-sm font-medium text-slate-100">{label}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-6 text-gray-900 shadow-2xl sm:p-8">
          {children}
        </section>
      </div>
    </main>
  )
}
