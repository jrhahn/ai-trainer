import { useT } from '../i18n'

/**
 * Legal notice and privacy policy, for the pages outside the app menu: sign-in,
 * sign-up, password reset and onboarding (ai-trainer-ops#1). § 5 DDG wants the
 * notice reachable from every page, without searching for it.
 *
 * Plain links into a new tab, not router links: onboarding renders without a
 * router, and a form half filled in on any of these pages stays as it was.
 */
export default function LegalLinks() {
  const t = useT()
  const linkClass = 'text-slate-400 hover:text-white'
  return (
    <nav aria-label={t('Legal')} className="flex justify-center gap-5 text-xs">
      <a href="/impressum" target="_blank" rel="noreferrer" className={linkClass}>
        {t('Legal notice')}
      </a>
      <a href="/privacy" target="_blank" rel="noreferrer" className={linkClass}>
        {t('Privacy policy')}
      </a>
    </nav>
  )
}
