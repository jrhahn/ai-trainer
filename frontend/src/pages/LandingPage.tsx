import { Link } from 'react-router-dom'
import { Apple, ArrowRight, Bike, HeartPulse, KeyRound, MessageCircle, RefreshCw, Target } from 'lucide-react'
import CoachChatPreview from '../components/CoachChatPreview'
import { GITHUB_URL, SETUP_GUIDE_URL } from '../utils/links'

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
    title: 'Knows your season',
    body: 'Set the target event once. From then on your coach builds towards that date and remembers the constraints you gave it — the hours you have, the days you cannot ride, the injury you are managing.',
  },
  {
    icon: MessageCircle,
    title: 'Answers on the spot',
    body: 'What to eat before a four-hour ride. Why your heart rate drifted on Sunday. Whether a niggle is worth a rest day. Ask in normal language and get an answer that accounts for your last four weeks.',
  },
  {
    icon: RefreshCw,
    title: 'Reworks the week',
    body: 'Missed Tuesday. Legs flat. Work trip. Say so and the plan moves, including everything downstream of it. No rebuilding a block by hand.',
  },
]

const steps = [
  {
    title: 'Connect once',
    body: 'intervals.icu pulls your rides straight from Strava, and we read them from there. Prefer to stay off both? Upload a FIT file instead.',
  },
  {
    title: 'Set the target',
    body: 'One conversation: the event, the date, the hours you can realistically train.',
  },
  {
    title: 'Ride',
    body: 'Tomorrow morning the session is waiting. Tell your coach how it went and the next one gets sharper.',
  },
]

const openSourceFacts = [
  {
    icon: KeyRound,
    title: 'Your key, your account',
    body: 'A Google Gemini key takes two minutes to create and costs nothing to start. Requests run on your account, not ours.',
  },
  {
    icon: GithubMark,
    title: 'AGPL-3.0, all of it',
    body: 'Every line is public. Audit the training logic, fork it, run the whole stack on your own hardware.',
  },
  {
    icon: HeartPulse,
    title: 'No lock-in',
    body: 'Self-host it, export it, or walk away. Your ride history was never ours to hold.',
  },
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
              Open source · Free to run
            </p>
            <h1 className="max-w-[15ch] text-balance text-4xl font-black leading-[1.05] tracking-tight sm:text-5xl lg:text-[3.5rem]">
              Coaching that keeps up with your week.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-slate-300">
              Talk to your coach the way you would talk to a person. It knows your history, your target event
              and how last week actually went, then plans tomorrow around it.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
              <Link
                to="/register"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-amber-500 px-6 py-3.5 text-base font-bold text-[#0f1116] transition-colors hover:bg-amber-400"
              >
                Create a free account
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link
                to="/login"
                className="inline-flex items-center justify-center rounded-lg border border-white/15 px-6 py-3.5 text-base font-semibold text-white transition-colors hover:bg-white/10"
              >
                Sign in
              </Link>
            </div>
            <p className="mt-5 text-sm text-slate-400">
              You bring a Google Gemini key. No subscription, no card, no trial period.
            </p>
          </div>

          <CoachChatPreview />
        </div>
      </section>

      <section className="border-t border-white/5 bg-[#12141b]">
        <div className="mx-auto w-full max-w-6xl px-5 py-20 sm:px-8">
          <h2 className="max-w-2xl text-balance text-3xl font-black tracking-tight sm:text-4xl">
            Built around your season, not a template.
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
            <h2 className="mt-4 text-3xl font-black tracking-tight sm:text-4xl">Ten minutes to set up.</h2>
            <p className="mt-5 max-w-md text-[15px] leading-7 text-slate-300">
              Rides sync in the background from then on, so your coach works from what you rode rather than
              what the plan said.
            </p>
            <span className="mt-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm text-slate-300">
              <Apple size={15} aria-hidden="true" className="text-amber-300" />
              Nutrition questions count too
            </span>
            <p className="mt-6 text-sm text-slate-400">
              <a
                href={SETUP_GUIDE_URL}
                target="_blank"
                rel="noreferrer"
                className="font-semibold text-amber-300 underline-offset-4 hover:underline"
              >
                Setup guide
              </a>{' '}
              — Strava, intervals.icu and your Gemini key, step by step.
            </p>
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
            What it costs: nothing.
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
          Put a date on the calendar.
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-[15px] leading-7 text-slate-300">
          Sign up, tell your coach what you are chasing, ride tomorrow's session.
        </p>
        <Link
          to="/register"
          className="mt-8 inline-flex items-center justify-center gap-2 rounded-lg bg-amber-500 px-7 py-3.5 text-base font-bold text-[#0f1116] transition-colors hover:bg-amber-400"
        >
          Create a free account
          <ArrowRight size={18} aria-hidden="true" />
        </Link>
      </section>

      <footer className="border-t border-white/5">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-5 py-8 text-sm text-slate-400 sm:flex-row sm:items-center sm:px-8">
          <span>Train Like a Pro — open-source cycling coaching, AGPL-3.0.</span>
          <a
            href={SETUP_GUIDE_URL}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-slate-300 hover:text-white sm:ml-auto"
          >
            Setup guide
          </a>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 font-medium text-slate-300 hover:text-white"
          >
            <GithubMark />
            Source on GitHub
          </a>
        </div>
      </footer>
    </main>
  )
}
