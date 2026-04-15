import { useState } from 'react'
import { Pencil, Check, X, Zap, Heart, Activity, HeartPulse } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { updateCurrentUser } from '../services/user'

// LTHR ≈ 87 % of max HR — must stay in sync with _LTHR_RATIO in backend/services/ai_service.py
const LTHR_RATIO = 0.87

const DEFAULT_FTP: Record<string, number> = {
  beginner: 150,
  intermediate: 220,
  advanced: 300,
}

interface MetricField {
  key: 'currentFTP' | 'maxHeartRate' | 'restingHeartRate' | 'thresholdHeartRate'
  label: string
  unit: string
  icon: React.ReactNode
  color: string
  placeholder: (profile: ReturnType<typeof buildDefaults>) => string
  hint?: string
}

function buildDefaults(fitnessLevel: string, maxHR: number | undefined) {
  return {
    defaultFTP: DEFAULT_FTP[fitnessLevel] ?? 220,
    defaultMaxHR: 185,
    defaultRestingHR: 60,
    derivedThresholdHR: maxHR ? Math.round(maxHR * LTHR_RATIO) : Math.round(185 * LTHR_RATIO),
  }
}

const METRICS: MetricField[] = [
  {
    key: 'currentFTP',
    label: 'FTP',
    unit: 'W',
    icon: <Zap size={18} />,
    color: 'text-purple-600 bg-purple-50',
    placeholder: (d) => `e.g. ${d.defaultFTP}`,
  },
  {
    key: 'maxHeartRate',
    label: 'Max HR',
    unit: ' bpm',
    icon: <Activity size={18} />,
    color: 'text-red-600 bg-red-50',
    placeholder: () => 'e.g. 185',
    hint: '220 − age',
  },
  {
    key: 'restingHeartRate',
    label: 'Resting HR',
    unit: ' bpm',
    icon: <Heart size={18} />,
    color: 'text-blue-600 bg-blue-50',
    placeholder: () => 'e.g. 60',
  },
  {
    key: 'thresholdHeartRate',
    label: 'Threshold HR',
    unit: ' bpm',
    icon: <HeartPulse size={18} />,
    color: 'text-orange-600 bg-orange-50',
    placeholder: (d) => `e.g. ${d.derivedThresholdHR}`,
    hint: '≈ 87% of Max HR',
  },
]

export default function FitnessMetricsCard() {
  const { authToken, userProfile, setUserProfile } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
      setUserProfile: s.setUserProfile,
    }))
  )

  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState({
    currentFTP: '',
    maxHeartRate: '',
    restingHeartRate: '',
    thresholdHeartRate: '',
  })

  const fitnessLevel = userProfile?.fitnessLevel ?? 'intermediate'
  const defaults = buildDefaults(fitnessLevel, userProfile?.maxHeartRate)

  const startEditing = () => {
    setDraft({
      currentFTP: userProfile?.currentFTP != null ? String(userProfile.currentFTP) : '',
      maxHeartRate: userProfile?.maxHeartRate != null ? String(userProfile.maxHeartRate) : '',
      restingHeartRate:
        userProfile?.restingHeartRate != null ? String(userProfile.restingHeartRate) : '',
      thresholdHeartRate:
        userProfile?.thresholdHeartRate != null ? String(userProfile.thresholdHeartRate) : '',
    })
    setError('')
    setEditing(true)
  }

  const cancel = () => setEditing(false)

  const save = async () => {
    if (!authToken || !userProfile) return

    const parse = (v: string) => (v.trim() === '' ? undefined : Number(v))
    const updates = {
      currentFTP: parse(draft.currentFTP),
      maxHeartRate: parse(draft.maxHeartRate),
      restingHeartRate: parse(draft.restingHeartRate),
      thresholdHeartRate: parse(draft.thresholdHeartRate),
    }

    const invalid = Object.entries(updates).find(
      ([, v]) => v !== undefined && (isNaN(v) || v <= 0)
    )
    if (invalid) {
      setError('All values must be positive numbers.')
      return
    }

    setSaving(true)
    setError('')
    try {
      await updateCurrentUser(authToken, updates)
      setUserProfile({ ...userProfile, ...updates })
      setEditing(false)
    } catch {
      setError('Failed to save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const displayValue = (key: MetricField['key']): string | null => {
    const v = userProfile?.[key]
    return v != null ? String(v) : null
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-gray-800">Fitness Metrics</h3>
        {!editing ? (
          <button
            onClick={startEditing}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg px-2 py-1 transition-colors"
          >
            <Pencil size={12} />
            Edit
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={cancel}
              disabled={saving}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg px-2 py-1 transition-colors"
            >
              <X size={12} />
              Cancel
            </button>
            <button
              onClick={save}
              disabled={saving}
              className="flex items-center gap-1 text-xs text-white bg-amber-500 hover:bg-amber-600 disabled:opacity-50 rounded-lg px-2 py-1 transition-colors"
            >
              <Check size={12} />
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        )}
      </div>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-3">
          {error}
        </p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {METRICS.map((m) => {
          const val = displayValue(m.key)
          const ph = m.placeholder(defaults)
          return (
            <div key={m.key} className="space-y-1">
              <div className="flex items-center gap-1.5">
                <div className={`w-6 h-6 rounded-md flex items-center justify-center ${m.color}`}>
                  {m.icon}
                </div>
                <span className="text-xs font-medium text-gray-600">{m.label}</span>
              </div>
              {editing ? (
                <div>
                  <input
                    type="number"
                    value={draft[m.key]}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, [m.key]: e.target.value }))
                    }
                    placeholder={ph}
                    className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:ring-amber-500 focus:border-amber-500"
                  />
                  {m.hint && <p className="text-xs text-gray-400 mt-0.5">{m.hint}</p>}
                </div>
              ) : (
                <p className="text-sm font-bold text-gray-900">
                  {val != null ? (
                    <>
                      {val}
                      <span className="font-normal text-gray-500">{m.unit}</span>
                    </>
                  ) : (
                    <span className="text-gray-400 font-normal text-xs">Not set</span>
                  )}
                </p>
              )}
            </div>
          )
        })}
      </div>

      {!editing && (
        <p className="text-xs text-gray-400 mt-3">
          Click <strong>Edit</strong> to update your FTP, heart-rate limits, and threshold HR. These
          values are used by the AI coach to tailor workout intensity.
        </p>
      )}
    </div>
  )
}
