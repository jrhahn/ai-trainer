import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Bike, Loader2 } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { register } from '../services/auth'
import { useAppStore } from '../store/useAppStore'

export default function RegisterPage() {
  const navigate = useNavigate()
  const setAuthToken = useAppStore((s) => s.setAuthToken)
  const loadUserData = useAppStore((s) => s.loadUserData)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

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
        // Authelia mode: account created, user must now sign in
        navigate('/login')
      }
    },
  })

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    registerMutation.mutate()
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#1a1a2e] to-[#16213e] flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="bg-amber-500 rounded-lg p-1.5">
            <Bike size={22} className="text-white" />
          </div>
          <span className="font-bold text-xl text-gray-900">AI Cycling Trainer</span>
        </div>

        <h1 className="text-2xl font-bold text-gray-900 mb-1">Create Account</h1>
        <p className="text-sm text-gray-500 mb-6">Register once and keep your training synced on the backend.</p>

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
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
              placeholder="At least 8 characters"
              minLength={8}
              required
            />
          </div>

          {registerMutation.error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {registerMutation.error instanceof Error ? registerMutation.error.message : 'Registration failed'}
            </div>
          )}

          <button
            type="submit"
            disabled={registerMutation.isPending}
            className="w-full bg-amber-500 text-white rounded-xl py-3 font-semibold flex items-center justify-center gap-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
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
    </div>
  )
}
