import type { DailyForecast } from '../store/useAppStore'

/**
 * Pure weather formatting shared by the logged-ride and planned-day views (#495).
 * Lives outside the component file so both sides format conditions identically.
 */

export function formatTemperature(value: number | null | undefined): string {
  if (value == null) return ''
  return `${Math.round(value)}°C`
}

/** Human-readable tooltip: "Forecast: rain · 11–16°C · 6.4 mm · 32 kph wind". */
export function describeForecast(forecast: DailyForecast): string {
  const parts: string[] = []
  if (forecast.condition) parts.push(forecast.condition.replace(/_/g, ' '))
  if (forecast.temperatureMinC != null && forecast.temperatureMaxC != null) {
    parts.push(
      `${Math.round(forecast.temperatureMinC)}–${Math.round(forecast.temperatureMaxC)}°C`,
    )
  }
  if (forecast.precipitationMm != null && forecast.precipitationMm > 0) {
    parts.push(`${forecast.precipitationMm} mm`)
  }
  if (forecast.windSpeedKph != null && forecast.windSpeedKph >= 25) {
    parts.push(`${Math.round(forecast.windSpeedKph)} kph wind`)
  }
  return parts.length > 0 ? `Forecast: ${parts.join(' · ')}` : 'Forecast'
}

/**
 * Coaching-relevant flags get a colour so an extreme day is visible at a glance
 * on the calendar. Unflagged days stay neutral — ordinary weather should not shout.
 */
export const WEATHER_LOAD_FLAG_STYLES: Record<string, string> = {
  very_hot: 'text-red-600',
  hot: 'text-orange-600',
  freezing: 'text-sky-600',
  cold: 'text-blue-600',
  rain: 'text-blue-600',
  snow: 'text-sky-600',
  thunderstorm: 'text-violet-600',
}
