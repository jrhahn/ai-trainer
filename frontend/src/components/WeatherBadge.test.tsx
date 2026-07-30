import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import WeatherBadge from './WeatherBadge'
import { describeForecast, formatTemperature } from '../utils/weather'
import type { DailyForecast } from '../store/useAppStore'

const hotDay: DailyForecast = {
  date: '2026-08-01',
  condition: 'clear',
  weatherCode: 0,
  temperatureMaxC: 38.6,
  temperatureMinC: 23.1,
  precipitationMm: 0,
  windSpeedKph: 9,
  loadFlag: 'very_hot',
}

const wetDay: DailyForecast = {
  date: '2026-08-02',
  condition: 'rain',
  weatherCode: 61,
  temperatureMaxC: 16.4,
  temperatureMinC: 11.2,
  precipitationMm: 6.4,
  windSpeedKph: 32,
  loadFlag: 'rain',
}

describe('formatTemperature', () => {
  it('rounds to a whole degree', () => {
    expect(formatTemperature(38.6)).toBe('39°C')
  })

  it('renders nothing for a missing reading rather than a zero', () => {
    expect(formatTemperature(null)).toBe('')
    expect(formatTemperature(undefined)).toBe('')
  })
})

describe('describeForecast', () => {
  it('summarises condition, range, precipitation and notable wind', () => {
    expect(describeForecast(wetDay)).toBe('Forecast: rain · 11–16°C · 6.4 mm · 32 kph wind')
  })

  it('omits calm wind and dry precipitation', () => {
    expect(describeForecast(hotDay)).toBe('Forecast: clear · 23–39°C')
  })

  it('omits the condition when none is known', () => {
    expect(
      describeForecast({ date: '2026-08-01', temperatureMinC: 11, temperatureMaxC: 16 }),
    ).toBe('Forecast: 11–16°C')
  })

  it('omits the range when only one end of it is known', () => {
    expect(describeForecast({ date: '2026-08-01', condition: 'rain', temperatureMaxC: 16 })).toBe(
      'Forecast: rain',
    )
  })

  it('degrades to a bare label when nothing at all is known', () => {
    expect(describeForecast({ date: '2026-08-01' })).toBe('Forecast')
  })
})

describe('WeatherBadge', () => {
  it('shows the high temperature for a planned day', () => {
    render(<WeatherBadge forecast={hotDay} />)
    expect(screen.getByText('39°C')).toBeInTheDocument()
  })

  it('renders nothing when the day is outside the forecast horizon', () => {
    const { container } = render(<WeatherBadge forecast={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the forecast carries no usable reading', () => {
    const { container } = render(
      <WeatherBadge forecast={{ date: '2026-08-01' }} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('exposes the full forecast as an accessible label', () => {
    render(<WeatherBadge forecast={wetDay} />)
    expect(
      screen.getByLabelText('Forecast: rain · 11–16°C · 6.4 mm · 32 kph wind'),
    ).toBeInTheDocument()
  })

  it('colours an extreme day so it stands out on the calendar', () => {
    render(<WeatherBadge forecast={hotDay} />)
    expect(screen.getByLabelText(/clear/)).toHaveClass('text-red-600')
  })

  it.each([
    ['clear', 'text-amber-500'],
    ['partly_cloudy', 'text-amber-500'],
    ['fog', 'text-gray-400'],
    ['rain', 'text-blue-500'],
    ['drizzle', 'text-blue-500'],
    ['snow', 'text-sky-500'],
    ['thunderstorm', 'text-violet-500'],
    ['cloudy', 'text-gray-400'],
    ['unknown', 'text-gray-400'],
  ])('renders a distinct icon for %s', (condition, iconClass) => {
    const { container } = render(
      <WeatherBadge forecast={{ date: '2026-08-01', condition, temperatureMaxC: 20 }} />,
    )
    expect(container.querySelector('svg')).toHaveClass(iconClass)
  })

  it('renders an icon for a day with a condition but no temperature', () => {
    const { container } = render(
      <WeatherBadge forecast={{ date: '2026-08-01', condition: 'rain' }} />,
    )
    expect(container.querySelector('svg')).toBeInTheDocument()
    expect(screen.queryByText(/°C/)).not.toBeInTheDocument()
  })

  it('falls back to neutral styling for an unrecognised load flag', () => {
    render(
      <WeatherBadge
        forecast={{
          date: '2026-08-01',
          condition: 'clear',
          temperatureMaxC: 20,
          loadFlag: 'meteor_shower',
        }}
      />,
    )
    expect(screen.getByLabelText(/clear/)).toHaveClass('text-gray-500')
  })

  it('keeps an ordinary day neutral', () => {
    render(
      <WeatherBadge
        forecast={{ date: '2026-08-03', condition: 'cloudy', temperatureMaxC: 19 }}
      />,
    )
    expect(screen.getByLabelText(/cloudy/)).toHaveClass('text-gray-500')
  })
})
