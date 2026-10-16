import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bike } from 'lucide-react'
import { fetchPowerCurve, type PowerCurve } from '../services/tools'
import { formatDecimal, slots, useLanguage, useT } from '../i18n'

// The free no-signup tool (ai-trainer-ops#25): one .fit in, its power-duration
// curve out. Nothing is stored and no model is called.

const W = 640
const H = 320
const PAD = { left: 48, right: 16, top: 16, bottom: 36 }
const TICKS: [number, string][] = [
  [5, '5s'], [30, '30s'], [60, '1m'], [300, '5m'], [1200, '20m'], [3600, '1h'], [7200, '2h'],
]

function label(seconds: number): string {
  return TICKS.find(([s]) => s === seconds)?.[1]
    ?? (seconds < 60 ? `${seconds}s` : `${Math.round(seconds / 60)}m`)
}

function Chart({ data, svgRef }: { data: PowerCurve; svgRef: React.Ref<SVGSVGElement> }) {
  const t = useT()
  const language = useLanguage()
  const all = [
    ...data.points.map((p) => p.watts),
    ...data.curve.flatMap((c) => [c.low, c.high]),
  ]
  const maxW = Math.max(...all) * 1.05
  const minS = Math.log(data.points[0].seconds)
  const maxS = Math.log(Math.max(data.points[data.points.length - 1].seconds, 1200))
  const x = (s: number) =>
    PAD.left + ((Math.log(s) - minS) / (maxS - minS || 1)) * (W - PAD.left - PAD.right)
  const y = (w: number) => PAD.top + (1 - w / maxW) * (H - PAD.top - PAD.bottom)
  const band = [
    ...data.curve.map((c) => `${x(c.seconds)},${y(c.high)}`),
    ...[...data.curve].reverse().map((c) => `${x(c.seconds)},${y(c.low)}`),
  ].join(' ')

  return (
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
      aria-label={t('Power-duration curve')} xmlns="http://www.w3.org/2000/svg">
      <rect width={W} height={H} fill="#0f1116" />
      {TICKS.filter(([s]) => s >= data.points[0].seconds && Math.log(s) <= maxS).map(([s, tick]) => (
        <g key={s}>
          <line x1={x(s)} x2={x(s)} y1={PAD.top} y2={H - PAD.bottom} stroke="#ffffff14" />
          <text x={x(s)} y={H - 14} fill="#94a3b8" fontSize="12" textAnchor="middle">{tick}</text>
        </g>
      ))}
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <text key={f} x={PAD.left - 6} y={y(maxW * f) + 4} fill="#94a3b8" fontSize="12" textAnchor="end">
          {Math.round(maxW * f)}
        </text>
      ))}
      {data.curve.length > 0 && <polygon points={band} fill="#f59e0b33" />}
      {data.curve.length > 0 && (
        <polyline fill="none" stroke="#f59e0b" strokeWidth="2" strokeDasharray="6 4"
          points={data.curve.map((c) => `${x(c.seconds)},${y(c.watts)}`).join(' ')} />
      )}
      <polyline fill="none" stroke="#ffffff" strokeWidth="2"
        points={data.points.map((p) => `${x(p.seconds)},${y(p.watts)}`).join(' ')} />
      {data.points.map((p) => (
        <circle key={p.seconds} cx={x(p.seconds)} cy={y(p.watts)} r="3" fill="#ffffff">
          <title>{`${label(p.seconds)}: ${p.watts} W`}</title>
        </circle>
      ))}
      {data.model && (
        <text x={W - PAD.right} y={PAD.top + 14} fill="#f8fafc" fontSize="14" textAnchor="end">
          {`CP ${data.model.cp} W (${data.model.cpLow}–${data.model.cpHigh}) · W′ ${formatDecimal(data.model.wPrime / 1000, 1, language)} kJ`}
        </text>
      )}
      <text x={W - PAD.right} y={H - 2} fill="#64748b" fontSize="10" textAnchor="end">trainlikea.pro/curve</text>
    </svg>
  )
}

function downloadPng(svg: SVGSVGElement) {
  const source = new XMLSerializer().serializeToString(svg)
  const img = new Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = W * 2
    canvas.height = H * 2
    canvas.getContext('2d')?.drawImage(img, 0, 0, canvas.width, canvas.height)
    const link = document.createElement('a')
    link.download = 'power-curve.png'
    link.href = canvas.toDataURL('image/png')
    link.click()
  }
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`
}

export default function PowerCurvePage() {
  const t = useT()
  const [data, setData] = useState<PowerCurve | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)

  async function onFile(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError('')
    setData(null)
    try {
      setData(await fetchPowerCurve(file))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="min-h-screen bg-[#0f1116] text-white">
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8">
        <Link to="/" className="flex items-center gap-2 text-sm font-semibold">
          <Bike size={20} aria-hidden="true" className="text-amber-400" /> Train Like a Pro
        </Link>
        <h1 className="mt-10 text-3xl font-semibold tracking-tight sm:text-4xl">{t('Your power-duration curve')}</h1>
        <p className="mt-4 text-slate-300">
          {t("Drop one ride's .fit file. You get its best power for every duration and a critical-power fit with its uncertainty. No account. The file is read once and not stored.")}
        </p>
        <label className="mt-8 inline-flex cursor-pointer items-center rounded-full bg-amber-500 px-6 py-3 font-semibold text-[#0f1116] hover:bg-amber-400">
          {busy ? t('Reading…') : t('Choose a .fit file')}
          <input type="file" accept=".fit" className="sr-only" disabled={busy}
            onChange={(e) => void onFile(e.target.files?.[0])} />
        </label>
        {error && <p role="alert" className="mt-4 text-red-300">{t(error)}</p>}
        {data && data.points.length > 0 && (
          <section className="mt-8">
            <Chart data={data} svgRef={svgRef} />
            <p className="mt-3 text-sm text-slate-400">
              {data.model
                ? t("White: this ride's best efforts. Amber: the critical-power model fitted to its 2–20 minute efforts, with a band of two standard errors. One ride shows what you did, not all you can do; a ride with all-out efforts gives a tighter band.")
                : t("White: this ride's best efforts. No critical-power fit: it needs at least three hard efforts between 2 and 20 minutes.")}
            </p>
            <button type="button" onClick={() => svgRef.current && downloadPng(svgRef.current)}
              className="mt-4 rounded-full border border-white/20 px-4 py-1.5 text-sm hover:bg-white/10">
              {t('Download as image')}
            </button>
          </section>
        )}
        <p className="mt-12 border-t border-white/10 pt-6 text-sm text-slate-400">
          {slots(t('The coach behind this fits the same model to every ride you do and plans your week around it. {link}.'), {
            link: (
              <Link key="link" to="/register" className="text-amber-400 hover:text-amber-300">
                {t('Try it')}
              </Link>
            ),
          })}
        </p>
      </div>
    </main>
  )
}
