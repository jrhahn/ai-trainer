import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MapPin, Save } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { fetchHomeLocation, saveHomeLocation } from '../services/user'

export const HOME_LOCATION_QUERY_KEY = 'athlete-home-location'

interface HomeLocationDraft {
  label: string
  latitude: string
  longitude: string
}

/**
 * The athlete's training location, editable (#495).
 *
 * The backend seeds this by clustering ride start points, which is right most of
 * the time and wrong exactly when it matters — after a move, or for someone whose
 * rides start at the office. Setting it here stores it as `user_set`, which the
 * backend then protects from every later inference pass, so an override sticks.
 */
export default function HomeLocationSettings() {
  const queryClient = useQueryClient()
  const { authToken, setHomeLocation } = useAppStore(
    useShallow((s) => ({ authToken: s.authToken, setHomeLocation: s.setHomeLocation })),
  )

  const { data: location, isLoading } = useQuery({
    queryKey: [HOME_LOCATION_QUERY_KEY, authToken],
    queryFn: () => fetchHomeLocation(authToken as string),
    enabled: Boolean(authToken),
  })

  // Only the athlete's edits are held in state; the stored location supplies the
  // defaults. Deriving rather than copying-on-load means the form shows the
  // inferred centroid the moment it arrives — the athlete corrects a visible
  // value instead of guessing — with no load-time state sync to get wrong.
  const [draft, setDraft] = useState<HomeLocationDraft | null>(null)
  const [error, setError] = useState<string | null>(null)

  const label = draft?.label ?? location?.label ?? ''
  const latitude = draft?.latitude ?? (location ? String(location.latitude) : '')
  const longitude = draft?.longitude ?? (location ? String(location.longitude) : '')
  const edit = (patch: Partial<HomeLocationDraft>) =>
    setDraft({ label, latitude, longitude, ...patch })

  const mutation = useMutation({
    mutationFn: () =>
      saveHomeLocation(authToken as string, {
        latitude: Number(latitude),
        longitude: Number(longitude),
        label: label.trim(),
      }),
    onSuccess: (saved) => {
      setHomeLocation(saved)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: [HOME_LOCATION_QUERY_KEY] })
    },
    onError: (err: unknown) =>
      setError(err instanceof Error ? err.message : 'Could not save your location'),
  })

  const parsedLat = Number(latitude)
  const parsedLng = Number(longitude)
  const coordinatesValid =
    latitude.trim() !== '' &&
    longitude.trim() !== '' &&
    Number.isFinite(parsedLat) &&
    Number.isFinite(parsedLng) &&
    Math.abs(parsedLat) <= 90 &&
    Math.abs(parsedLng) <= 180
  const canSave = Boolean(authToken) && coordinatesValid && !mutation.isPending

  const sourceNote = !location
    ? 'Not set yet — your weather outlook falls back to the last ride with GPS.'
    : location.source === 'user_set'
      ? 'Set by you. Automatic inference will not overwrite it.'
      : location.source === 'inferred'
        ? `Inferred from ${location.rideCount ?? 0} clustered ride starts (confidence ${location.confidence.toFixed(2)}). Set it here to override.`
        : 'Taken from your most recent ride with GPS. Set it here to make it permanent.'

  return (
    <section className="bg-white rounded-2xl border border-gray-100 p-5 mt-6">
      <div className="flex items-center gap-2 mb-1">
        <MapPin className="w-5 h-5 text-emerald-600" />
        <h3 className="text-base font-semibold text-gray-900">Training location</h3>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Where your weather forecast is taken from. You can also just tell the coach
        &ldquo;I mostly train near Freiburg now&rdquo;.
      </p>

      {isLoading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : (
        <>
          <p className="text-xs text-gray-500 mb-3">{sourceNote}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-xs font-semibold text-gray-600">
              Place name
              <input
                type="text"
                value={label}
                onChange={(e) => edit({ label: e.target.value })}
                placeholder="Freiburg"
                className="mt-1 w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500"
              />
            </label>
            <label className="text-xs font-semibold text-gray-600">
              Latitude
              <input
                type="number"
                step="0.0001"
                value={latitude}
                onChange={(e) => edit({ latitude: e.target.value })}
                className="mt-1 w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500"
              />
            </label>
            <label className="text-xs font-semibold text-gray-600">
              Longitude
              <input
                type="number"
                step="0.0001"
                value={longitude}
                onChange={(e) => edit({ longitude: e.target.value })}
                className="mt-1 w-full rounded-lg border-gray-300 text-sm focus:border-amber-500 focus:ring-amber-500"
              />
            </label>
          </div>

          {error && <p className="mt-3 text-xs font-medium text-red-600">{error}</p>}

          <div className="flex justify-end mt-4">
            <button
              type="button"
              onClick={() => mutation.mutate()}
              disabled={!canSave}
              className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
            >
              <Save className="w-4 h-4" />
              {mutation.isPending ? 'Saving…' : 'Save location'}
            </button>
          </div>
        </>
      )}
    </section>
  )
}
