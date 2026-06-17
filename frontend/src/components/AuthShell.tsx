import type { ReactNode } from 'react'
import { Activity, Bike, CalendarCheck, Sparkles, TrendingUp, Zap } from 'lucide-react'
import heroImage from '../assets/hero.png'

type AuthShellProps = {
  children: ReactNode
  eyebrow: string
  title: string
  subtitle: string
}

const highlights = [
  { label: 'Strava, Intervals.icu, and FIT', icon: Activity },
  { label: 'Coach chat anytime', icon: CalendarCheck },
  { label: 'Learns from your feedback', icon: TrendingUp },
]

export default function AuthShell({ children, eyebrow, title, subtitle }: AuthShellProps) {
  return (
    <main className="min-h-screen bg-[#111318] text-white">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col justify-center gap-6 px-4 py-6 sm:px-6 lg:grid lg:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.75fr)] lg:items-center lg:gap-10 lg:py-8">
        <section className="relative overflow-hidden rounded-lg border border-white/10 bg-[#171a20] shadow-2xl">
          <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(245,158,11,0.22),transparent_34%),linear-gradient(315deg,rgba(20,184,166,0.14),transparent_42%)]" />
          <div className="relative grid min-h-[460px] content-between gap-8 p-6 sm:p-8 lg:min-h-[620px]">
            <div>
              <div className="mb-10 flex items-center gap-2">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500 text-[#111318]">
                  <Bike size={21} aria-hidden="true" />
                </span>
                <span className="text-lg font-bold tracking-normal">Train Like a Pro</span>
              </div>

              <p className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">{eyebrow}</p>
              <h1 className="max-w-xl text-4xl font-black leading-tight tracking-normal text-white sm:text-5xl">
                {title}
              </h1>
              <p className="mt-4 max-w-xl text-base leading-7 text-slate-200">{subtitle}</p>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(220px,0.65fr)] lg:items-end">
              <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-1">
                {highlights.map(({ label, icon: Icon }) => (
                  <div
                    key={label}
                    className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.08] px-3 py-2.5 backdrop-blur"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white text-[#111318]">
                      <Icon size={17} aria-hidden="true" />
                    </span>
                    <span className="text-sm font-semibold text-white">{label}</span>
                  </div>
                ))}
              </div>

              <div className="relative mx-auto w-full max-w-[240px]">
                <img
                  src={heroImage}
                  alt=""
                  className="mx-auto h-auto w-full max-w-[220px] drop-shadow-[0_22px_28px_rgba(0,0,0,0.34)]"
                />
                <div className="absolute bottom-5 left-0 right-0 mx-auto w-[92%] rounded-lg border border-white/20 bg-[#111318]/[0.88] px-3 py-2 shadow-xl backdrop-blur">
                  <div className="mb-2 flex items-center justify-between text-xs text-slate-300">
                    <span>Today</span>
                    <span className="flex items-center gap-1 text-emerald-300">
                      <Sparkles size={13} aria-hidden="true" />
                      Ready
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <Metric label="Load" value="74" />
                    <Metric label="Form" value="+8" />
                    <Metric label="Next" value="Tempo" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-5 text-gray-900 shadow-2xl sm:p-6">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-amber-600">Training account</p>
              <p className="mt-1 text-sm text-gray-500">Secure access to your coach, plans, and ride data.</p>
            </div>
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
              <Zap size={19} aria-hidden="true" />
            </span>
          </div>
          {children}
        </section>
      </div>
    </main>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white px-2 py-1.5 text-[#111318]">
      <div className="text-sm font-black leading-none">{value}</div>
      <div className="mt-1 text-[10px] font-semibold uppercase tracking-normal text-gray-500">{label}</div>
    </div>
  )
}
