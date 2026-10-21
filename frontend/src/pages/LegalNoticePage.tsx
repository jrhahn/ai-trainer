import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useLegalContact } from '../hooks/useLegalContact'
import { useLanguage, useT } from '../i18n'
import { addressLines } from '../utils/addressLines'

/**
 * The legal notice under § 5 DDG (ai-trainer-ops#1).
 *
 * Name, postal address and email come from the deployment's configuration
 * (`OPERATOR_NAME`, `OPERATOR_ADDRESS`, `CONTACT_EMAIL`), so the owner sets
 * them as variables and the page never guesses one. What is not set says so.
 * Reachable signed in and signed out: the landing page, every sign-in page,
 * onboarding and the app menu link here.
 */
export default function LegalNoticePage() {
  const t = useT()
  const language = useLanguage()
  const contact = useLegalContact()
  const german = language === 'de'
  const missing = german ? '(noch nicht eingetragen)' : '(not set yet)'
  const lines = addressLines(contact?.operatorAddress)
  const email = contact?.contactEmail

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft size={16} aria-hidden="true" /> {t('Back')}
        </Link>
        <article className="text-sm leading-6 text-gray-700">
          <h1 className="mt-6 text-2xl font-bold text-gray-900">{german ? 'Impressum' : 'Legal notice'}</h1>

          <h2 className="mt-8 text-lg font-semibold text-gray-900">
            {german ? 'Angaben gemäß § 5 DDG' : 'Information under § 5 DDG (German Digital Services Act)'}
          </h2>
          <address className="mt-2 not-italic">
            <div>{contact?.operatorName ?? <em>{missing}</em>}</div>
            {lines.length > 0 ? lines.map((line, index) => <div key={index}>{line}</div>) : <div><em>{missing}</em></div>}
          </address>

          <h2 className="mt-8 text-lg font-semibold text-gray-900">{german ? 'Kontakt' : 'Contact'}</h2>
          <p className="mt-2">
            {german ? 'E-Mail: ' : 'Email: '}
            {email ? (
              <a className="text-amber-700 underline" href={`mailto:${email}`}>
                {email}
              </a>
            ) : (
              <em>{missing}</em>
            )}
          </p>

          <p className="mt-8">
            <Link to="/privacy" className="text-amber-700 underline">
              {t('Privacy policy')}
            </Link>
          </p>
        </article>
      </div>
    </main>
  )
}
