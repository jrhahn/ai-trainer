import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { LineChart } from './LineChart'

describe('LineChart', () => {
  it('returns null when there are no finite values', () => {
    const { container } = render(<LineChart data={[]} labels={[]} color="#000" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders an svg with the yLabel as accessible label', () => {
    const { getByLabelText } = render(
      <LineChart data={[1, 2, 3]} labels={['a', 'b', 'c']} color="#000" yLabel="FTP" />
    )
    expect(getByLabelText('FTP').tagName.toLowerCase()).toBe('svg')
  })

  it('falls back to a default aria-label when no yLabel is given', () => {
    const { getByLabelText } = render(
      <LineChart data={[1, 2]} labels={['a', 'b']} color="#000" />
    )
    expect(getByLabelText('Line chart')).toBeInTheDocument()
  })

  it('draws one point circle per data value for a single series', () => {
    const { container } = render(
      <LineChart data={[1, 2, 3, 4]} labels={['a', 'b', 'c', 'd']} color="#111" />
    )
    expect(container.querySelectorAll('circle')).toHaveLength(4)
    expect(container.querySelectorAll('polyline')).toHaveLength(1)
  })

  it('renders second and third series when provided', () => {
    const { container } = render(
      <LineChart
        data={[1, 2, 3]}
        data2={[2, 3, 4]}
        color2="#0f0"
        data3={[3, 4, 5]}
        color3="#00f"
        labels={['a', 'b', 'c']}
        color="#111"
      />
    )
    expect(container.querySelectorAll('polyline')).toHaveLength(3)
    // 3 points per series
    expect(container.querySelectorAll('circle')).toHaveLength(9)
  })

  it('draws a zero reference line when zeroLine is set and the range crosses zero', () => {
    const { container } = render(
      <LineChart data={[-5, 0, 8]} labels={['a', 'b', 'c']} color="#111" zeroLine />
    )
    const dashed = Array.from(container.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke-dasharray') === '4 2'
    )
    expect(dashed).toHaveLength(1)
  })

  it('omits the zero line when all values are positive', () => {
    const { container } = render(
      <LineChart data={[3, 5, 8]} labels={['a', 'b', 'c']} color="#111" zeroLine />
    )
    const dashed = Array.from(container.querySelectorAll('line')).filter(
      (l) => l.getAttribute('stroke-dasharray') === '4 2'
    )
    expect(dashed).toHaveLength(0)
  })

  it('renders de-duplicated first/middle/last x-axis labels', () => {
    const { getByText } = render(
      <LineChart data={[1, 2, 3, 4, 5]} labels={['Jan', 'Feb', 'Mar', 'Apr', 'May']} color="#111" />
    )
    expect(getByText('Jan')).toBeInTheDocument()
    expect(getByText('Mar')).toBeInTheDocument() // middle of 5
    expect(getByText('May')).toBeInTheDocument()
  })
})
