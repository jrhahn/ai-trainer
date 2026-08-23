import { Link } from 'react-router-dom'
import { Apple, ArrowRight, Bike, HeartPulse, KeyRound, MessageCircle, RefreshCw, Target } from 'lucide-react'
import CoachChatPreview from '../components/CoachChatPreview'

const GITHUB_URL = 'https://github.com/jrhahn/ai-trainer'

function GithubMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  )
}

const pillars = [
  {
    icon: Target,
    title: 'It learns who you are',
    body: 'Tell it about the race you circled on the calendar, the 5 a.m. starts, the knee that complains on long climbs. It remembers, and every session it writes is built around that.',
  },
  {
    icon: MessageCircle,
    title: 'Ask it anything',
    body: 'Why does my heart rate drift? Should I eat before an early ride? Is this saddle pain normal? It answers training and nutrition questions in plain language, using your own rides as context.',
  },
  {
    icon: RefreshCw,
    title: 'It changes its mind with you',
    body: 'Short on time, sore, travelling, or suddenly full of energy? Say so, and tomorrow’s session changes. No rigid twelve-week block you quietly abandon in week three.',
  },
]

const steps = [
  {
    title: 'Connect your rides',
    body: 'Link intervals.icu once and your Strava rides flow in automatically. No Strava? Drop in a FIT file and it works the same.',
  },
  {
    title: 'Tell the coach about you',
    body: 'A short conversation about your goal, your week, and what you actually enjoy riding. That is the whole setup.',
  },
  {
    title: 'Ride what it gives you',
    body: 'Every morning there is one session waiting that fits your form, your calendar, and the weather outside your door.',
  },
]

const openSourceFacts = [
  { icon: KeyRound, title: 'Your own Gemini key', body: 'Bring a free Google Gemini API key. No subscription, no per-month fee, no upsell.' },
  { icon: GithubMark, title: 'Open source, AGPL-3.0', body: 'Read every line, host it yourself, change what you disagree with. Nothing is hidden behind a paywall.' },
  { icon: HeartPulse, title: 'Your data stays yours', body: 'Run it on your own machine or server. Your rides and conversations live where you put them.' },
]

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[#0f1116] text-white">
      <header className="mx-auto flex w-full max-w-6xl items-center gap-4 px-5 py-5 sm:px-8">
        <span className="flex shrink-0 items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500 text-[#0f1116]">
            <Bike size={20} aria-hidden="true" />
          </span>
          <span className="whitespace-nowrap text-base font-bold sm:text-lg">Train Like a Pro</span>
        </span>
        <nav className="ml-auto flex shrink-0 items-center gap-3 sm:gap-5">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="hidden items-center gap-2 text-sm font-medium text-slate-300 hover:text-white sm:flex"
          >
            <GithubMark />
            GitHub
          </a>
          <Link to="/login" className="whitespace-nowrap text-sm font-semibold text-slate-200 hover:text-white">
            Sign in
          </Link>
          <Link
            to="/register"
            className="whitespace-nowrap rounded-lg bg-amber-500 px-4 py-2 text-sm font-bold text-[#0f1116] transition-colors hover:bg-amber-400"
          >
            Get started
          </Link>
        </nav>
      </header>

      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_36rem_at_18%_-10%,rgba(245,158,11,0.22),transparent_60%),radial-gradient(48rem_32rem_at_88%_18%,rgba(20,184,166,0.16),transparent_62%)]" />
        <div className="relative mx-auto grid w-full max-w-6xl gap-12 px-5 pb-20 pt-12 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.85fr)] lg:items-center lg:pt-20">
          <div>
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.06] px-3 py-1 text-xs font-semibold text-amber-300">
              Free forever · Open source
            </p>
            <h1 className="max-w-[15ch] text-balance text-4xl font-black leading-[1.05] tracking-tight sm:text-5xl lg:text-[3.5rem]">
              A cycling coach that actually knows you.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-slate-300">
              Not a spreadsheet of numbers. A coach you talk to — one that learns your goals, your week, and
              how your body answers back, and puts the right session in front of you every single day.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
              <Link
                to="/register"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-amber-500 px-6 py-3.5 text-base font-bold text-[#0f1116] transition-colors hover:bg-amber-400"
              >
                Start training free
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link
                to="/login"
                className="inline-flex items-center justify-center rounded-lg border border-white/15 px-6 py-3.5 text-base font-semibold text-white transition-colors hover:bg-white/10"
              >
                I already have an account
              </Link>
            </div>
            <p className="mt-5 text-sm text-slate-400">
              All you need is a free Google Gemini key. No subscription, no credit card.
            </p>
          </div>

          <CoachChatPreview />
        </div>
      </section>

      <section className="border-t border-white/5 bg-[#12141b]">
        <div className="mx-auto w-full max-w-6xl px-5 py-20 sm:px-8">
          <h2 className="max-w-2xl text-3xl font-black tracking-tight sm:text-4xl">
            Most training apps hand you a plan. This one asks about your day.
          </h2>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {pillars.map(({ icon: Icon, title, body }) => (
              <div key={title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber-500/15 text-amber-300">
                  <Icon size={21} aria-hidden="true" />
                </span>
                <h3 className="mt-5 text-xl font-bold">{title}</h3>
                <p className="mt-3 text-[15px] leading-7 text-slate-300">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-5 py-20 sm:px-8">
        <div className="grid gap-12 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1fr)] lg:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">Getting started</p>
            <h2 className="mt-4 text-3xl font-black tracking-tight sm:text-4xl">Three steps, then just ride.</h2>
            <p className="mt-5 max-w-md text-[15px] leading-7 text-slate-300">
              Your rides sync themselves in the background, so the coach always knows what you actually did —
              not what you meant to do.
            </p>
            <span className="mt-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm text-slate-300">
              <Apple size={15} aria-hidden="true" className="text-amber-300" />
              Training and nutrition questions welcome, any hour
            </span>
          </div>

          <ol className="space-y-4">
            {steps.map(({ title, body }, index) => (
              <li key={title} className="flex gap-5 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500 text-sm font-black text-[#0f1116]">
                  {index + 1}
                </span>
                <div>
                  <h3 className="text-lg font-bold">{title}</h3>
                  <p className="mt-2 text-[15px] leading-7 text-slate-300">{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="border-y border-white/5 bg-[#12141b]">
        <div className="mx-auto w-full max-w-6xl px-5 py-20 sm:px-8">
          <h2 className="max-w-2xl text-3xl font-black tracking-tight sm:text-4xl">
            Free, and it stays that way.
          </h2>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {openSourceFacts.map(({ icon: Icon, title, body }) => (
              <div key={title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-400/15 text-emerald-300">
                  <Icon size={21} aria-hidden="true" />
                </span>
                <h3 className="mt-5 text-xl font-bold">{title}</h3>
                <p className="mt-3 text-[15px] leading-7 text-slate-300">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-5 py-20 text-center sm:px-8">
        <h2 className="mx-auto max-w-2xl text-3xl font-black tracking-tight sm:text-4xl">
          Tell it what you are training for.
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-[15px] leading-7 text-slate-300">
          Two minutes to sign up, one conversation to get going, and a session waiting for you tomorrow morning.
        </p>
        <Link
          to="/register"
          className="mt-8 inline-flex items-center justify-center gap-2 rounded-lg bg-amber-500 px-7 py-3.5 text-base font-bold text-[#0f1116] transition-colors hover:bg-amber-400"
        >
          Start training free
          <ArrowRight size={18} aria-hidden="true" />
        </Link>
      </section>

      <footer className="border-t border-white/5">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-5 py-8 text-sm text-slate-400 sm:flex-row sm:items-center sm:px-8">
          <span>Train Like a Pro — open-source cycling coaching, AGPL-3.0.</span>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 font-medium text-slate-300 hover:text-white sm:ml-auto"
          >
            <GithubMark />
            Source on GitHub
          </a>
        </div>
      </footer>
    </main>
  )
}
