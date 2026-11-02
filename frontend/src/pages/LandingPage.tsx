import { Link } from 'react-router-dom'
import { ArrowRight, Bike } from 'lucide-react'
import RideLandscape from '../components/RideLandscape'
import { GITHUB_URL, setupGuideUrl } from '../utils/links'
import { useLanguage, useT } from '../i18n'
import { facts, moments } from '../utils/publicCopy'

export default function LandingPage() {
  const t = useT()
  const language = useLanguage()
  return (
    <main className="landing min-h-screen bg-[#0f1116] text-white">
      <section className="relative isolate flex min-h-[92vh] flex-col overflow-hidden">
        <RideLandscape className="absolute inset-x-0 bottom-0 -z-10 h-[62%] w-full" />

        <header className="mx-auto flex w-full max-w-6xl items-center gap-4 px-5 py-5 sm:px-8">
          {/* `min-w-0` + `truncate` so the wordmark gives way at 390 px instead of
              the page scrolling sideways (ai-trainer-ops#51.3). */}
          <span className="flex min-w-0 items-center gap-2">
            <Bike size={22} aria-hidden="true" className="shrink-0 text-amber-400" />
            <span className="truncate text-base font-semibold tracking-tight sm:text-lg">Train Like a Pro</span>
          </span>
          <nav className="ml-auto flex shrink-0 items-center gap-4 sm:gap-6">
            {/* From `sm` up only: on a phone it cut the name to "Train Li…", and
                the hero has its own "Sign in" under the main button (review of
                2026-11-01, ai-trainer-ops#338 A6). */}
            <Link
              to="/login"
              data-testid="header-sign-in"
              className="hidden whitespace-nowrap text-sm font-medium text-slate-300 hover:text-white sm:inline"
            >
              {t('Sign in')}
            </Link>
            <Link
              to="/register"
              className="whitespace-nowrap rounded-full border border-white/20 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-white/10"
            >
              {t('Create account')}
            </Link>
          </nav>
        </header>

        <div className="mx-auto grid w-full max-w-6xl gap-12 px-5 pb-16 pt-14 sm:px-8 sm:pt-24 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div>
            {/* The promise, not "AI coaching": everyone claims that
                (ai-trainer-ops#19, the owner's headline of 2026-10-27). */}
            <h1 className="max-w-[16ch] text-balance text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">
              {t('Training that moves with your week.')}
            </h1>
            <p className="mt-6 max-w-lg text-lg leading-8 text-slate-300">
              {/* "Nothing to fill in" while onboarding asks four questions
                  (owner, 2026-10-23). */}
              {t("A long day at work, a party, three days ill: the week is planned again around what happened, and the coach says why in one sentence. Runs and strength days included. Four short questions, and you're set.")}
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-x-6 gap-y-3">
              <Link
                to="/register"
                className="inline-flex items-center gap-2 rounded-full bg-amber-500 px-6 py-3 text-base font-semibold text-[#0f1116] transition-colors hover:bg-amber-400"
              >
                {t('Create a free account')}
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link to="/login" className="text-base font-medium text-slate-300 hover:text-white">
                {t('Sign in')}
              </Link>
            </div>
          </div>

          <figure className="max-w-sm lg:mt-10">
            <blockquote className="rounded-2xl rounded-bl-sm bg-white/[0.07] px-5 py-4 text-[15px] leading-7 text-slate-100 backdrop-blur-sm">
              {t('A party on Monday? Then Tuesday goes easy, and the intervals move to Thursday.')}
            </blockquote>
            <figcaption className="mt-2 pl-1 text-xs text-slate-400">{t('Your coach, Sunday evening')}</figcaption>
          </figure>
        </div>
      </section>

      {/* The product itself (ai-trainer-ops#53 item 4, #81 Q19): a synthetic athlete's
          dashboard, taken by e2e/stubbed/landing-screenshot.spec.ts. In the
          reader's language, and on a phone the phone's own frame: the desktop
          picture in English came out at about 3 px text there (review of
          2026-11-01, ai-trainer-ops#338 A5). */}
      <section className="mx-auto w-full max-w-6xl px-5 pt-24 sm:px-8">
        <figure>
          <picture>
            <source
              media="(max-width: 639px)"
              srcSet={`/landing-dashboard-phone${language === 'de' ? '-de' : ''}.png`}
              width={390}
              height={805}
            />
            <img
              src={`/landing-dashboard${language === 'de' ? '-de' : ''}.png`}
              width={1366}
              height={719}
              loading="lazy"
              alt={t("The dashboard: today's sweet spot session with its focus points and goal, and the week around it.")}
              className="w-full rounded-2xl border border-white/10 shadow-2xl"
            />
          </picture>
          <figcaption className="mt-3 text-sm text-slate-400">
            {t("Today's session and your week, as the coach plans them.")}
          </figcaption>
        </figure>
      </section>

      <section className="mx-auto w-full max-w-6xl px-5 py-24 sm:px-8">
        <ul className="grid gap-12 md:grid-cols-3 md:gap-10">
          {moments.map(({ title, body }) => (
            <li key={title} className="border-t border-white/15 pt-6">
              <h2 className="text-lg font-semibold">{t(title)}</h2>
              <p className="mt-3 text-[15px] leading-7 text-slate-400">{t(body)}</p>
            </li>
          ))}
        </ul>
      </section>

      <footer className="border-t border-white/10">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-5 py-10 text-sm text-slate-400 sm:px-8 md:flex-row md:items-end md:justify-between">
          <div className="max-w-xl space-y-1.5">
            {facts.map((fact) => (
              <p key={fact}>{t(fact)}</p>
            ))}
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            <Link to="/curve" className="text-slate-300 hover:text-white">
              {t('Free power-curve tool')}
            </Link>
            <Link to="/privacy" className="text-slate-300 hover:text-white">
              {t('Privacy policy')}
            </Link>
            <Link to="/impressum" className="text-slate-300 hover:text-white">
              {t('Legal notice')}
            </Link>
            <a href={setupGuideUrl(language)} target="_blank" rel="noreferrer" className="text-slate-300 hover:text-white">
              {t('Setup guide')}
            </a>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="text-slate-300 hover:text-white">
              {t('Source available, FSL-1.1-ALv2')}
            </a>
          </div>
        </div>
      </footer>
    </main>
  )
}
