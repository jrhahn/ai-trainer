import { useState, type FocusEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { input } from './ui'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { fetchHomeLocation, saveHomeLocation } from '../services/user'
import { useSaveStatus } from '../hooks/useSaveStatus'
import SaveStatus from './SaveStatus'
import { formatDecimal, useLanguage, useT } from '../i18n'
import { HOME_LOCATION_QUERY_KEY } from '../queries/keys'


interface HomeLocationDraft {
  label: string
  latitude: string
  longitude: string
  /** Whether the athlete typed coordinates; otherwise the name is looked up. */
  coordinatesEdited: boolean
}

/**
 * The athlete's training location, editable (#495).
 *
 * The backend seeds this by clustering ride start points, which is right most of
 * the time and wrong exactly when it matters — after a move, or for someone whose
 * rides start at the office. Setting it here stores it as `user_set`, which the
 * backend then protects from every later inference pass, so an override sticks.
 *
 * Saved when focus leaves the three fields, like every setting on the page
 * (ai-trainer-ops#93): the place and its coordinates are one value, so moving
 * from latitude to longitude is still the same edit.
 */
export default function HomeLocationSettings() {
  const queryClient = useQueryClient()
  const t = useT()
  const language = useLanguage()
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
  const status = useSaveStatus()

  const label = draft?.label ?? location?.label ?? ''
  const latitude = draft?.latitude ?? (location ? String(location.latitude) : '')
  const longitude = draft?.longitude ?? (location ? String(location.longitude) : '')
  const coordinatesEdited = draft?.coordinatesEdited ?? false
  const edit = (patch: Partial<HomeLocationDraft>) => {
    setDraft({ label, latitude, longitude, coordinatesEdited, ...patch })
    status.clearError()
  }

  const parsedLat = Number(latitude)
  const parsedLng = Number(longitude)
  const coordinatesValid =
    latitude.trim() !== '' &&
    longitude.trim() !== '' &&
    Number.isFinite(parsedLat) &&
    Number.isFinite(parsedLng) &&
    Math.abs(parsedLat) <= 90 &&
    Math.abs(parsedLng) <= 180

  const commit = (event: FocusEvent<HTMLDivElement>) => {
    // Still inside the group: the athlete is moving to the next coordinate.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    if (!draft || !authToken) return
    // A town alone is enough: the server looks it up. Nobody knows their own
    // coordinates, and a name with untouched ones used to fail on blur
    // (review of 2026-11-01, ai-trainer-ops#338 A11).
    if (!draft.coordinatesEdited && !label.trim()) {
      status.fail('Enter a place name, such as the nearest town.')
      return
    }
    if (draft.coordinatesEdited && !coordinatesValid) {
      status.fail('Enter a latitude between -90 and 90 and a longitude between -180 and 180.')
      return
    }
    void status.run(async () => {
      const saved = await saveHomeLocation(
        authToken,
        draft.coordinatesEdited
          ? { latitude: parsedLat, longitude: parsedLng, label: label.trim() }
          : { label: label.trim() },
      )
      setHomeLocation(saved)
      setDraft(null)
      await queryClient.invalidateQueries({ queryKey: [HOME_LOCATION_QUERY_KEY] })
    })
  }

  const sourceNote = !location
    ? t('Not set yet — your weather outlook falls back to the last ride with GPS.')
    : location.source === 'user_set'
      ? t('Set by you. Automatic inference will not overwrite it.')
      : location.source === 'inferred'
        ? t('Inferred from {count} clustered ride starts (confidence {confidence}). Set it here to override.', {
            count: location.rideCount ?? 0,
            confidence: formatDecimal(location.confidence, 2, language),
          })
        : t('Taken from your most recent ride with GPS. Set it here to make it permanent.')

  return (
    // The same card as the others around it: no icon, no colour of its own.
    <section className="bg-white rounded-2xl shadow-xs border border-gray-100 p-6">
      <h3 className="text-base font-semibold text-gray-900 mb-1">{t('Training location')}</h3>
      <p className="text-sm text-gray-500 mb-4">
        {t('Where your weather forecast is taken from. You can also just tell the coach “I mostly train near Freiburg now”.')}
      </p>

      {isLoading ? (
        <p className="text-sm text-gray-500">{t('Loading…')}</p>
      ) : (
        <>
          <p className="text-sm text-gray-500 mb-3">{sourceNote}</p>
          <div className="space-y-3" onBlur={commit}>
            <label className="block text-sm font-medium text-gray-700">
              {t('Place name')}
              <input
                type="text"
                value={label}
                onChange={(e) => edit({ label: e.target.value })}
                placeholder="Freiburg"
                className={`mt-1 font-normal ${input}`}
              />
            </label>
            {/* For the few who want a spot more exact than a town. */}
            <details className="text-sm">
              <summary className="cursor-pointer text-gray-500">{t('Coordinates (optional)')}</summary>
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <label className="text-sm font-medium text-gray-700">
                  {t('Latitude')}
                  <input
                    type="number"
                    step="0.0001"
                    value={latitude}
                    onChange={(e) => edit({ latitude: e.target.value, coordinatesEdited: true })}
                    className={`mt-1 font-normal ${input}`}
                  />
                </label>
                <label className="text-sm font-medium text-gray-700">
                  {t('Longitude')}
                  <input
                    type="number"
                    step="0.0001"
                    value={longitude}
                    onChange={(e) => edit({ longitude: e.target.value, coordinatesEdited: true })}
                    className={`mt-1 font-normal ${input}`}
                  />
                </label>
              </div>
            </details>
          </div>

          <SaveStatus state={status.state} />
        </>
      )}
    </section>
  )
}
