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
import {
  WEATHER_LOAD_FLAG_STYLES,
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
 */

export function WeatherIcon({
  condition,
  size = 12,
}: {
  condition?: string | null
  size?: number
}) {
  const normalized = condition?.toLowerCase()
  if (normalized === 'clear') return <Sun size={size} className="text-amber-500" />
  if (normalized === 'partly_cloudy') {
    return <CloudSun size={size} className="text-amber-500" />
  }
  if (normalized === 'fog') return <CloudFog size={size} className="text-gray-400" />
  if (normalized === 'rain' || normalized === 'drizzle') {
    return <CloudRain size={size} className="text-blue-500" />
  }
  if (normalized === 'snow') return <CloudSnow size={size} className="text-sky-500" />
  if (normalized === 'thunderstorm') {
    return <CloudLightning size={size} className="text-violet-500" />
  }
  return <Cloud size={size} className="text-gray-400" />
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
  if (!forecast) return null
  const temperature = formatTemperature(forecast.temperatureMaxC)
  if (!forecast.condition && !temperature) return null
  const flagStyle = forecast.loadFlag
    ? WEATHER_LOAD_FLAG_STYLES[forecast.loadFlag] ?? ''
    : ''
  const description = describeForecast(forecast)
  return (
    <span
      title={description}
      aria-label={description}
      className={`inline-flex items-center gap-0.5 ${flagStyle || 'text-gray-500'} ${className}`}
    >
      <WeatherIcon condition={forecast.condition} size={size} />
      {temperature}
    </span>
  )
}
