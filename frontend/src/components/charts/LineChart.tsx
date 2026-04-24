// ---------------------------------------------------------------------------
// Shared SVG line-chart used by ProgressionChart and TrainingLoadChart.
// ---------------------------------------------------------------------------

export interface LineChartProps {
  data: number[]
  labels: string[]
  color: string
  /** Optional second series rendered as a dashed line */
  data2?: number[]
  color2?: string
  /** Optional third series rendered as a dotted line */
  data3?: number[]
  color3?: string
  height?: number
  yLabel?: string
  /** Draw a horizontal reference line at y=0 (useful for TSB) */
  zeroLine?: boolean
}

export function LineChart({
  data,
  labels,
  color,
  data2,
  color2,
  data3,
  color3,
  height = 80,
  yLabel,
  zeroLine = false,
}: LineChartProps) {
  const width = 400
  const padX = 8
  const padY = 10

  const allValues = [
    ...data,
    ...(data2 ?? []),
    ...(data3 ?? []),
  ].filter((v) => isFinite(v))

  if (allValues.length === 0) return null

  const minVal = Math.min(...allValues)
  const maxVal = Math.max(...allValues)
  const range = maxVal - minVal || 1

  const toX = (i: number, total: number) =>
    padX + (i / Math.max(total - 1, 1)) * (width - padX * 2)
  const toY = (v: number) =>
    padY + ((maxVal - v) / range) * (height - padY * 2)

  const polyline = (values: number[]) =>
    values.map((v, i) => `${toX(i, values.length)},${toY(v)}`).join(' ')

  const zeroY =
    zeroLine && minVal < 0 && maxVal > 0 ? toY(0) : null

  return (
    <div className="w-full overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ minWidth: '240px' }}
        aria-label={yLabel ?? 'Line chart'}
      >
        {/* Horizontal grid lines */}
        {[0, 0.5, 1].map((t) => {
          const y = padY + t * (height - padY * 2)
          const val = maxVal - t * range
          return (
            <g key={t}>
              <line
                x1={padX}
                y1={y}
                x2={width - padX}
                y2={y}
                stroke="#e5e7eb"
                strokeWidth="0.5"
              />
              <text x={padX} y={y - 2} fontSize="6" fill="#9ca3af">
                {Math.round(val)}
              </text>
            </g>
          )
        })}

        {/* Zero reference line (e.g. for TSB) */}
        {zeroY !== null && (
          <line
            x1={padX}
            y1={zeroY}
            x2={width - padX}
            y2={zeroY}
            stroke="#6b7280"
            strokeWidth="0.8"
            strokeDasharray="4 2"
          />
        )}

        {/* Series 1 */}
        <polyline
          points={polyline(data)}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.map((v, i) => (
          <circle key={i} cx={toX(i, data.length)} cy={toY(v)} r="2" fill={color} />
        ))}

        {/* Series 2 — dashed */}
        {data2 && color2 && (
          <>
            <polyline
              points={polyline(data2)}
              fill="none"
              stroke={color2}
              strokeWidth="1.5"
              strokeDasharray="3 2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {data2.map((v, i) => (
              <circle key={i} cx={toX(i, data2.length)} cy={toY(v)} r="1.5" fill={color2} />
            ))}
          </>
        )}

        {/* Series 3 — dotted */}
        {data3 && color3 && (
          <>
            <polyline
              points={polyline(data3)}
              fill="none"
              stroke={color3}
              strokeWidth="1.5"
              strokeDasharray="1.5 3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {data3.map((v, i) => (
              <circle key={i} cx={toX(i, data3.length)} cy={toY(v)} r="1.5" fill={color3} />
            ))}
          </>
        )}

        {/* X-axis labels — show first, middle, last */}
        {[0, Math.floor((labels.length - 1) / 2), labels.length - 1]
          .filter((i, pos, arr) => arr.indexOf(i) === pos && i < labels.length)
          .map((i) => (
            <text
              key={i}
              x={toX(i, labels.length)}
              y={height - 1}
              fontSize="6"
              fill="#9ca3af"
              textAnchor="middle"
            >
              {labels[i]}
            </text>
          ))}
      </svg>
    </div>
  )
}
