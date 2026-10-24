import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle2, Eye, Loader2, XCircle } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import AuthShell from '../components/AuthShell'
import { CONFIRM_EMAIL, register } from '../services/auth'
import { useInviteOnly } from '../hooks/useInviteOnly'
import { useAppStore } from '../store/useAppStore'
import { useT } from '../i18n'
import { getPasswordChecks, revealWhileHeld, serverPasswordMessage } from '../utils/passwordChecks'

export default function RegisterPage() {
  const t = useT()
  const navigate = useNavigate()
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const loadUserData = useAppStore((s) => s.loadUserData)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isPasswordVisible, setIsPasswordVisible] = useState(false)
  const [isConfirmPasswordVisible, setIsConfirmPasswordVisible] = useState(false)
  // A closed round signs up by invitation (ops#108, #96). The link the owner
  // sends carries the code, so a friend never has to type it.
  const [searchParams] = useSearchParams()
  const [inviteCode, setInviteCode] = useState(searchParams.get('invite') ?? '')
  const inviteOnly = useInviteOnly()
  // Health data need explicit consent (Art. 9(2)(a) GDPR, ai-trainer-ops#2).
  const [privacyConsent, setPrivacyConsent] = useState(false)

  const passwordChecks = getPasswordChecks(password, name, email)
  const isPasswordStrong = passwordChecks.every((check) => check.valid)
  const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword
  const showMismatch = confirmPassword.length > 0 && password !== confirmPassword
  const formIsValid = Boolean(
    name && email && isPasswordStrong && passwordsMatch && privacyConsent && (!inviteOnly || inviteCode.trim())
  )

  const registerMutation = useMutation({
    mutationFn: async () => {
      const token = await register(name, email, password, inviteCode, privacyConsent)
      if (token && token !== CONFIRM_EMAIL) {
        setAuthToken(token)
        await loadUserData(token)
      }
      return token
    },
    onSuccess: (token) => {
      if (token === CONFIRM_EMAIL) {
        // The server sent a link first (app #687); the sign-in page says so.
        navigate('/login', { state: { confirmEmail: email } })
      } else if (token) {
        navigate('/onboarding')
      } else {
        // Authelia mode: account created, user now signs in through Authelia
        // so the auth portal creates the session used by the app.
        navigate('/login')
      }
    },
  })
  const canSubmit = formIsValid && !registerMutation.isPending

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    registerMutation.mutate()
  }


  return (
    <AuthShell
      eyebrow={inviteOnly ? t('Closed test · by invitation only') : t('Free · You bring a Gemini key')}
      title={t('Start with the date you are training for.')}
      subtitle={t('Tell your coach what you are training for and how your weeks really look. The first plan lands a couple of minutes later.')}
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">{t('Create Account')}</h2>
        <p className="text-sm text-gray-500 mb-5">{t('One account, and your training stays in sync on every device.')}</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {(inviteOnly || inviteCode) && (
            <div>
              <label htmlFor="register-invite" className="block text-sm font-medium text-gray-700 mb-1">
                {t('Invitation code')}
              </label>
              <input
                id="register-invite"
                type="text"
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                value={inviteCode}
                onChange={(event) => setInviteCode(event.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono tracking-wider focus:ring-amber-500 focus:border-amber-500"
                placeholder="ABCD-EFGH-JK"
                required={inviteOnly}
              />
              <p className="text-xs text-gray-500 mt-1">{t('From the invitation you were sent.')}</p>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Name')}</label>
            <input
              type="text"
              autoComplete="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder={t('Your name')}
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Email')}</label>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="you@example.com"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Password')}</label>
            <div className="relative">
              <input
                type={isPasswordVisible ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full border border-gray-300 rounded-lg pl-3 pr-11 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                autoComplete="new-password"
                placeholder={t('Strong password')}
                minLength={10}
                required
              />
              <button
                type="button"
                aria-label={t('Hold to show password')}
                aria-pressed={isPasswordVisible}
                className="absolute inset-y-0 right-0 w-10 flex items-center justify-center text-gray-500 hover:text-gray-800"
                {...revealWhileHeld(setIsPasswordVisible)}
              >
                <Eye size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-1.5 text-xs">
              {passwordChecks.map((check) => (
                <div key={check.id} className={check.valid ? 'flex items-center gap-2 text-green-700' : 'flex items-center gap-2 text-gray-500'}>
                  {check.valid ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
                  <span>{t(check.label, check.vars)}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('Confirm password')}</label>
            <div className="relative">
              <input
                type={isConfirmPasswordVisible ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className={`w-full border rounded-lg pl-3 pr-11 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 ${
                  showMismatch ? 'border-red-300 bg-red-50' : 'border-gray-300'
                }`}
                autoComplete="new-password"
                placeholder={t('Repeat your password')}
                minLength={10}
                required
                aria-invalid={showMismatch}
              />
              <button
                type="button"
                aria-label={t('Hold to show confirmation password')}
                aria-pressed={isConfirmPasswordVisible}
                className="absolute inset-y-0 right-0 w-10 flex items-center justify-center text-gray-500 hover:text-gray-800"
                {...revealWhileHeld(setIsConfirmPasswordVisible)}
              >
                <Eye size={18} aria-hidden="true" />
              </button>
            </div>
            {showMismatch && <p className="text-xs text-red-600 mt-1">{t('The two passwords do not match.')}</p>}
            {passwordsMatch && <p className="text-xs text-green-700 mt-1">{t('Passwords match.')}</p>}
          </div>

          <div className="flex items-start gap-2">
            <input
              id="register-privacy-consent"
              type="checkbox"
              checked={privacyConsent}
              onChange={(event) => setPrivacyConsent(event.target.checked)}
              className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-amber-500 focus:ring-amber-500"
              required
            />
            <div className="text-xs leading-5 text-gray-600">
              <label htmlFor="register-privacy-consent">
                {t('I have read the privacy policy and agree that my training and health data (heart rate, effort, how I feel) are processed to make my plan.')}
              </label>{' '}
              <Link to="/privacy" target="_blank" className="font-medium text-amber-700 underline">
                {t('Read the privacy policy')}
              </Link>
            </div>
          </div>

          {registerMutation.error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {registerMutation.error instanceof Error
                ? serverPasswordMessage(t, registerMutation.error.message)
                : t('Registration failed')}
            </div>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full bg-amber-500 text-[#111318] rounded-lg py-3 font-bold flex items-center justify-center gap-2 hover:bg-amber-400 disabled:opacity-50 transition-colors"
          >
            {registerMutation.isPending ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                {t('Creating account...')}
              </>
            ) : (
              t('Create Account')
            )}
          </button>
        </form>

        <p className="text-sm text-gray-500 mt-6 text-center">
          {t('Already have an account?')}{' '}
          <Link to="/login" className="text-amber-600 hover:text-amber-700 font-semibold">
            {t('Sign in')}
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
