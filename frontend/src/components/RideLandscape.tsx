/**
 * The landing page's one visual (ai-trainer-ops#67): ridgelines drawn from
 * elevation-profile-like curves, with a single route traced along the nearest.
 *
 * There are no own photos or videos yet. Stock photography would be generic,
 * which is the opposite of "a coach for you", so the picture comes from the
 * domain instead: it reads as a ride, costs no licence, and a real photo or
 * video can replace this component later without touching the layout.
 *
 * The curves are a fixed sum of sines, so the picture is the same on every
 * render and on the server.
 */

const WIDTH = 1440
const HEIGHT = 560
const STEP = 16

function ridge(base: number, waves: [number, number, number][]): number[] {
  const points: number[] = []
  for (let x = 0; x <= WIDTH; x += STEP) {
    points.push(waves.reduce((y, [amp, period, phase]) => y - amp * Math.sin((x / period) * Math.PI * 2 + phase), base))
  }
  return points
}

function area(ys: number[]): string {
  return `M0,${HEIGHT} ` + ys.map((y, i) => `L${i * STEP},${y.toFixed(1)}`).join(' ') + ` L${WIDTH},${HEIGHT} Z`
}

function line(ys: number[], from: number, to: number): string {
  return ys
    .slice(from, to)
    .map((y, i) => `${i === 0 ? 'M' : 'L'}${(from + i) * STEP},${(y - 6).toFixed(1)}`)
    .join(' ')
}

const LAYERS = [
  {
    ys: ridge(250, [
      [70, 900, 0.4],
      [30, 310, 1.1],
      [12, 130, 2.0],
    ]),
    fill: 'var(--ridge-1)',
  },
  {
    ys: ridge(330, [
      [60, 1100, 2.2],
      [26, 380, 0.3],
      [10, 150, 1.7],
    ]),
    fill: 'var(--ridge-2)',
  },
  {
    ys: ridge(410, [
      [55, 1300, 4.0],
      [22, 420, 2.6],
      [8, 170, 0.9],
    ]),
    fill: 'var(--ridge-3)',
  },
]
// The nearest ridge climbs from the left, and the route ends on its summit.
const FRONT = ridge(470, [
  [70, 2400, -1.2],
  [14, 460, 3.3],
  [5, 190, 0.2],
])
const ROUTE_FROM = 4
const ROUTE_TO = FRONT.indexOf(Math.min(...FRONT)) + 1

export default function RideLandscape({ className = '' }: { className?: string }) {
  const end = FRONT[ROUTE_TO - 1]
  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="xMidYMax slice"
      aria-hidden="true"
      focusable="false"
      className={`ride-landscape ${className}`}
    >
      {LAYERS.map(({ ys, fill }) => (
        <path key={fill} d={area(ys)} fill={fill} />
      ))}
      <path d={area(FRONT)} fill="var(--ridge-4)" />
      <path d={line(FRONT, ROUTE_FROM, ROUTE_TO)} className="ride-landscape-route" fill="none" />
      <circle cx={(ROUTE_TO - 1) * STEP} cy={end - 6} r="5" fill="#f59e0b" />
    </svg>
  )
}
