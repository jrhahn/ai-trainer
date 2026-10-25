import { useEffect, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { useShallow } from 'zustand/shallow'
import { button, chip } from '../components/ui'
import { useAppStore } from '../store/useAppStore'
import { useProfile } from '../queries/profile'
import { LANGUAGE_CHOICES, useT, type LanguageChoice } from '../i18n'
import AIKeySettings from '../components/AIKeySettings'
import TotpSettings from '../components/TotpSettings'
import SessionSettings from '../components/SessionSettings'
import PasswordSettings from '../components/PasswordSettings'
import AthleteTraitsSettings, { MemoryDataSettings } from '../components/AthleteTraitsSettings'
import CoachReviewConsentCard from '../components/CoachReviewConsentCard'
import MotivationModelSettings from '../components/MotivationModelSettings'
import AthleteModelSettings from '../components/AthleteModelSettings'
import AthletePerformanceModelCard from '../components/AthletePerformanceModelCard'
import ConversationImportSettings from '../components/ConversationImportSettings'
import HomeLocationSettings from '../components/HomeLocationSettings'
import TrainingAnswersSettings from '../components/TrainingAnswersSettings'
import ProfileFields from '../components/settings/ProfileFields'
import HeartRateFields from '../components/settings/HeartRateFields'
import DataSources from '../components/settings/DataSources'
import AiProviderCard from '../components/settings/AiProviderCard'
import { DangerZone, ExportCard } from '../components/settings/AccountData'
import { signOut } from '../services/sessions'
import { useMetricsPipeline } from '../hooks/useMetricsPipeline'

// Five sections and nothing else without Expert mode (ai-trainer-ops#50). The
// page used to be seventeen cards in one 7,200 px column, ordered by when each
// was built: the display name sat below a provider picker, and an irreversible
// "Recalculate TSS / ATL / CTL" stood between an athlete and their upload.
const SECTIONS = [
  { id: 'profile', title: 'Profile' },
  { id: 'activities', title: 'Activities' },
  { id: 'coach', title: 'Coach & AI' },
  { id: 'account', title: 'Account & security' },
  { id: 'data', title: 'Your data' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

const sectionAnchor = (id: SectionId) => `settings-${id}`
// How long a linked section is looked for: 20 × 100 ms.
const SCROLL_TRIES = 20
const SCROLL_RETRY_MS = 100

/** Side nav from `lg`, tabs that wrap above that. Plain anchors: the browser
 *  already scrolls to them and keeps them in the history.
 *
 *  Wrapping and not a sideways-scrolling strip: a scroll container still
 *  counts its content toward the page's minimum width, so the strip made the
 *  whole page 551 px wide at 390 (seen in the #803 screenshots). */
function SectionNav() {
  const t = useT()
  return (
    <nav
      aria-label={t('Settings sections')}
      className="sticky top-14 z-10 -mx-4 mb-6 bg-gray-100 px-4 py-2 md:top-0 lg:top-8 lg:mx-0 lg:mb-0 lg:w-48 lg:shrink-0 lg:p-0"
    >
      <ul className="flex flex-wrap gap-1 lg:flex-col">
        {SECTIONS.map((section) => (
          <li key={section.id}>
            <a
              href={`#${sectionAnchor(section.id)}`}
              className="block whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-white hover:text-gray-900"
            >
              {t(section.title)}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function Section({ id, children }: { id: SectionId; children: ReactNode }) {
  const t = useT()
  const title = t(SECTIONS.find((section) => section.id === id)!.title)
  return (
    <section
      id={sectionAnchor(id)}
      aria-labelledby={`${sectionAnchor(id)}-heading`}
      // Clears the fixed mobile header plus the sticky tab strip on a jump.
      className="space-y-4 scroll-mt-28 md:scroll-mt-16 lg:scroll-mt-8"
    >
      <h2 id={`${sectionAnchor(id)}-heading`} className="text-lg font-bold text-gray-900">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** Which language the interface speaks (ai-trainer-ops#103 Q27). Applies at
 * once, like every other setting here (#871). */
function LanguageSettings() {
  const t = useT()
  const uiLanguage = useAppStore((s) => s.uiLanguage)
  const setUiLanguage = useAppStore((s) => s.setUiLanguage)
  const labels: Record<LanguageChoice, string> = {
    auto: t('Automatic (browser)'),
    en: 'English',
    de: 'Deutsch',
  }
  return (
    <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Language')}</h3>
      <p className="text-sm text-gray-500 mb-3">
        {t('The interface follows your browser unless you choose here. Your coach answers in the language you write in.')}
      </p>
      <div role="radiogroup" aria-label={t('Language')} className="flex flex-wrap gap-2">
        {LANGUAGE_CHOICES.map((choice) => (
          <button
            key={choice}
            type="button"
            role="radio"
            aria-checked={uiLanguage === choice}
            onClick={() => setUiLanguage(choice)}
            className={chip(uiLanguage === choice)}
          >
            {labels[choice]}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function SettingsPage() {
  // A link from elsewhere in the app names a section (`/settings#settings-…`,
  // ai-trainer-ops#157). An in-app navigation does not scroll to a hash the way
  // a page load does, so do it once the page is there.
  const { hash } = useLocation()
  useEffect(() => {
    if (!hash) return
    // The section may render only once its data is in, so look again for a
    // moment rather than once.
    let tries = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const find = () => {
      const target = document.getElementById(hash.slice(1))
      if (target) target.scrollIntoView()
      else if (++tries < SCROLL_TRIES) timer = setTimeout(find, SCROLL_RETRY_MS)
    }
    find()
    return () => clearTimeout(timer)
  }, [hash])
  const t = useT()
  const userProfile = useProfile()
  const { isExpertMode, logout } = useAppStore(
    useShallow((s) => ({ isExpertMode: s.isExpertMode, logout: s.logout })),
  )
  // One pipeline for the page: the FTP card's recalculation and the heart-rate
  // card's confirmation share its `isPending`, so they cannot overlap.
  const pipeline = useMetricsPipeline()
  const handleLogout = () => signOut(logout)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('Settings')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('Your profile, where your activities come from, the coach, and your account')}</p>
      </div>

      <div className="lg:flex lg:items-start lg:gap-8">
        <SectionNav />
        <div className="min-w-0 max-w-2xl flex-1 space-y-10">
        <Section id="profile">
          <LanguageSettings />
          {/* The onboarding answers (ai-trainer-ops#202). */}
          <TrainingAnswersSettings />
          <ProfileFields pipeline={pipeline} />
          <HeartRateFields pipeline={pipeline} />

          {/* Editable training location behind the weather forecast (#495) */}
          <HomeLocationSettings />
        </Section>

        <Section id="activities">
          <DataSources />
        </Section>

        <Section id="coach">
          {/* Per-user AI API key (BYOK) */}
          <AIKeySettings />

          {isExpertMode && <AiProviderCard />}

          {/* What the athlete trains FOR (#562/#567). Ahead of the learned traits
              on purpose: everything below describes the athlete, this decides what
              all of it is in service of. Goals, limits and what the coach inferred
              are the coach's working state: expert mode (ai-trainer-ops#217). The
              chat is where an athlete says what they train for. */}
          {isExpertMode && <MotivationModelSettings />}

          {/* Learned athlete traits */}
          <AthleteTraitsSettings />

          {isExpertMode && (
            <>
              {/* Import historical coach conversations */}
              <ConversationImportSettings />

              {/* Long-term structured athlete model (#384) */}
              <AthleteModelSettings />

              {/* Deterministic performance model, limiter & hypotheses (#481) */}
              <AthletePerformanceModelCard />
            </>
          )}
        </Section>

        <Section id="account">
          <div className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
            <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Account')}</h3>
            <p className="text-sm text-gray-500 mb-4">{t('Signed in as {email}.', { email: userProfile?.email ?? t('unknown') })}</p>

            <button onClick={handleLogout} className={button}>
              {t('Sign Out')}
            </button>
          </div>

          {/* Ending sessions elsewhere (#704). Immediately after the Account card
              on purpose: this is where someone looks when they want to be signed
              out, and the first person to go looking for it clicked "Sign Out"
              above instead. */}
          <SessionSettings />

          {/* Next to the sessions, because a new password ends them all
              (ai-trainer-ops#172). */}
          <PasswordSettings />

          <TotpSettings />
        </Section>

        <Section id="data">
          {/* Your data (ai-trainer-ops#6): export before the danger zone, so the
              way to keep a copy sits right above the way to delete everything.
              The coach review consent (ai-trainer-ops#136) comes first, shown
              only while the opt-in is switched on. The coach's memory, as a
              file or cleared, sits between them (ai-trainer-ops#217). */}
          <CoachReviewConsentCard />
          <ExportCard />

          <MemoryDataSettings />

          <DangerZone />
        </Section>
        </div>
      </div>
    </div>
  )
}
