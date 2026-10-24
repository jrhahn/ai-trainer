import {
  Cloud,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Sun,
} from 'lucide-react'
import type { DailyForecast } from '../store/useAppStore'
import { useLanguage, useT } from '../i18n'
import {
  WEATHER_LOAD_FLAGS,
  describeForecast,
  formatTemperature,
} from '../utils/weather'

/**
 * Shared weather presentation for both sides of a training day (#495).
 *
 * Logged activities carry the conditions they were actually ridden in; planned
 * days carry the upcoming forecast. Both render through the same icon map and the
 * same temperature format, so a planned Saturday and the ride that happens on it
 * look like the same kind of information rather than two unrelated widgets.
 *
 * The icons take the text colour around them. The shape says the weather.
 */

export function WeatherIcon({
  condition,
  size = 12,
}: {
  condition?: string | null
  size?: number
}) {
  const normalized = condition?.toLowerCase()
  if (normalized === 'clear') return <Sun size={size} />
  if (normalized === 'partly_cloudy') return <CloudSun size={size} />
  if (normalized === 'fog') return <CloudFog size={size} />
  if (normalized === 'rain' || normalized === 'drizzle') return <CloudRain size={size} />
  if (normalized === 'snow') return <CloudSnow size={size} />
  if (normalized === 'thunderstorm') return <CloudLightning size={size} />
  return <Cloud size={size} />
}

/**
 * Icon + high temperature for a planned day. Renders nothing when the day is
 * outside the forecast horizon — an absent forecast must look absent, never like
 * a guess.
 */
export default function WeatherBadge({
  forecast,
  size = 12,
  className = '',
}: {
  forecast: DailyForecast | undefined
  size?: number
  className?: string
}) {
  const t = useT()
  const language = useLanguage()
  if (!forecast) return null
  const temperature = formatTemperature(forecast.temperatureMaxC)
  if (!forecast.condition && !temperature) return null
  const flagged = Boolean(forecast.loadFlag && WEATHER_LOAD_FLAGS.has(forecast.loadFlag))
  const description = describeForecast(forecast, t, language)
  return (
    <span
      title={description}
      aria-label={description}
      className={`inline-flex items-center gap-0.5 ${flagged ? 'font-semibold text-gray-900' : 'text-gray-500'} ${className}`}
    >
      <WeatherIcon condition={forecast.condition} size={size} />
      {temperature}
    </span>
  )
}
