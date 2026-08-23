import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { CheckCircle2, Eye, Loader2, XCircle } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import AuthShell from '../components/AuthShell'
import { register } from '../services/auth'
import { useAppStore } from '../store/useAppStore'

const weakTerms = ['password', 'qwerty', 'admin', 'welcome', 'trainlikea']

function getPasswordChecks(password: string, name: string, email: string) {
  const lowerPassword = password.toLowerCase()
  const emailLocal = email.split('@')[0]?.toLowerCase() ?? ''
  const nameParts = name.toLowerCase().split(/[^a-z0-9]+/).filter((part) => part.length >= 3)
  const personalParts = [emailLocal, ...emailLocal.split(/[^a-z0-9]+/), ...nameParts].filter((part) => part.length >= 3)

  return [
    { id: 'length', label: 'At least 8 characters', valid: password.length >= 8 },
    { id: 'lowercase', label: 'One lowercase letter', valid: /[a-z]/.test(password) },
    { id: 'uppercase', label: 'One uppercase letter', valid: /[A-Z]/.test(password) },
    { id: 'number', label: 'One number', valid: /\d/.test(password) },
    { id: 'special', label: 'One special character', valid: /[^A-Za-z0-9]/.test(password) },
    {
      id: 'trivial',
      label: 'No common words, name, or email',
      valid:
        !weakTerms.some((term) => lowerPassword.includes(term)) &&
        !personalParts.some((part) => lowerPassword.includes(part)),
    },
  ]
}

export default function RegisterPage() {
  const navigate = useNavigate()
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const loadUserData = useAppStore((s) => s.loadUserData)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isPasswordVisible, setIsPasswordVisible] = useState(false)
  const [isConfirmPasswordVisible, setIsConfirmPasswordVisible] = useState(false)

  const passwordChecks = getPasswordChecks(password, name, email)
  const isPasswordStrong = passwordChecks.every((check) => check.valid)
  const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword
  const showMismatch = confirmPassword.length > 0 && password !== confirmPassword
  const formIsValid = Boolean(name && email && isPasswordStrong && passwordsMatch)

  const registerMutation = useMutation({
    mutationFn: async () => {
      const token = await register(name, email, password)
      if (token) {
        setAuthToken(token)
        await loadUserData(token)
      }
      return token
    },
    onSuccess: (token) => {
      if (token) {
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

  const passwordRevealHandlers = (setVisible: (visible: boolean) => void) => ({
    onPointerDown: () => setVisible(true),
    onPointerUp: () => setVisible(false),
    onPointerLeave: () => setVisible(false),
    onPointerCancel: () => setVisible(false),
    onBlur: () => setVisible(false),
    onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault()
        setVisible(true)
      }
    },
    onKeyUp: (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault()
        setVisible(false)
      }
    },
  })

  return (
    <AuthShell
      eyebrow="Free · You bring a Gemini key"
      title="Set the target. Let the coach handle the rest."
      subtitle="Tell it what you are training for and how your weeks really look. Every session from there is built on your data and gets sharper as you ride."
    >
      <div>
        <h2 className="text-2xl font-bold text-gray-900 mb-1">Create Account</h2>
        <p className="text-sm text-gray-500 mb-5">One account, and your training stays in sync on every device.</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="Your name"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="you@example.com"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <div className="relative">
              <input
                type={isPasswordVisible ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full border border-gray-300 rounded-lg pl-3 pr-11 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
                placeholder="Strong password"
                minLength={8}
                required
              />
              <button
                type="button"
                aria-label="Hold to show password"
                aria-pressed={isPasswordVisible}
                className="absolute inset-y-0 right-0 w-10 flex items-center justify-center text-gray-500 hover:text-gray-800"
                {...passwordRevealHandlers(setIsPasswordVisible)}
              >
                <Eye size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-1.5 text-xs">
              {passwordChecks.map((check) => (
                <div key={check.id} className={check.valid ? 'flex items-center gap-2 text-green-700' : 'flex items-center gap-2 text-gray-500'}>
                  {check.valid ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
                  <span>{check.label}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Confirm password</label>
            <div className="relative">
              <input
                type={isConfirmPasswordVisible ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                className={`w-full border rounded-lg pl-3 pr-11 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 ${
                  showMismatch ? 'border-red-300 bg-red-50' : 'border-gray-300'
                }`}
                placeholder="Repeat your password"
                minLength={8}
                required
                aria-invalid={showMismatch}
              />
              <button
                type="button"
                aria-label="Hold to show confirmation password"
                aria-pressed={isConfirmPasswordVisible}
                className="absolute inset-y-0 right-0 w-10 flex items-center justify-center text-gray-500 hover:text-gray-800"
                {...passwordRevealHandlers(setIsConfirmPasswordVisible)}
              >
                <Eye size={18} aria-hidden="true" />
              </button>
            </div>
            {showMismatch && <p className="text-xs text-red-600 mt-1">The two passwords do not match.</p>}
            {passwordsMatch && <p className="text-xs text-green-700 mt-1">Passwords match.</p>}
          </div>

          {registerMutation.error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {registerMutation.error instanceof Error ? registerMutation.error.message : 'Registration failed'}
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
                Creating account...
              </>
            ) : (
              'Create Account'
            )}
          </button>
        </form>

        <p className="text-sm text-gray-500 mt-6 text-center">
          Already have an account?{' '}
          <Link to="/login" className="text-amber-600 hover:text-amber-700 font-semibold">
            Sign in
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
