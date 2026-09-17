import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { log, traced } from './log'
import type { GateTestInfo, WaveCapture, WaveSignal, Waveform as Wave } from './types'
import { useT } from './i18n'

/**
 * Waveform viewer (ported from hls-rtl), in the shape engineers know from Verdi / SimVision:
 * a fixed signal-name column, a scrolling time canvas, a draggable cursor with
 * a per-signal value readout, and hex bus values drawn inside the bus shape.
 *
 * Canvas rather than SVG: a regression window is tens of thousands of edges and
 * one DOM node per edge does not survive a zoom drag.
 */

const ROW_H = 24
const RULER_H = 26
const PAD = 4
// The name and value gutters scale with the pane so a narrow window spends its
// pixels on the waveform itself, while a wide one stops widening the gutters.
const nameWidth = (w: number) => Math.round(Math.min(210, Math.max(112, w * 0.20)))
const valueWidth = (w: number) => Math.round(Math.min(110, Math.max(64, w * 0.10)))

function hex(bits: string): string {
  if (/[xz]/.test(bits)) return bits.includes('x') ? 'x' : 'z'
  if (!/^[01]+$/.test(bits)) return bits
  let out = ''
  // pad to a nibble boundary so the leading digit is not mis-weighted
  const padded = bits.padStart(Math.ceil(bits.length / 4) * 4, '0')
  for (let i = 0; i < padded.length; i += 4) out += parseInt(padded.slice(i, i + 4), 2).toString(16)
  return out.replace(/^0+(?=.)/, '')
}

/** Value of `sig` at time `t` — the last transition at or before t. */
function valueAt(sig: WaveSignal, t: number): string {
  const tr = sig.transitions
  let lo = 0, hi = tr.length - 1, best = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (tr[mid][0] <= t) { best = mid; lo = mid + 1 } else { hi = mid - 1 }
  }
  return best < 0 ? '-' : tr[best][1]
}

function css(el: HTMLElement, name: string, fallback: string): string {
  const v = getComputedStyle(el).getPropertyValue(name).trim()
  return v || fallback
}

const MODULES = ['g711_alaw_enc', 'g711_alaw_dec', 'g711_ulaw_enc', 'g711_ulaw_dec']

export interface WaveRequest { test?: string; candidate?: string; testbench?: string; module?: string }

/**
 * Capture = one testbench run traced inside a window (pipeline/waves.py). A failing run
 * is centred on its first mismatch; a passing one shows reset and the first cycles.
 */
export function WaveformPane({ request }: { request?: WaveRequest | null }) {
  const t = useT()
  const [wave, setWave] = useState<Wave | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [cursor, setCursor] = useState(0)
  const [view, setView] = useState({ start: 0, span: 2000000 })
  const [selected, setSelected] = useState<string | null>(null)
  const [tests, setTests] = useState<GateTestInfo[]>([])
  const [captures, setCaptures] = useState<WaveCapture[]>([])
  const [source, setSource] = useState<'gate' | 'testbench'>('gate')
  const [test, setTest] = useState('L1_ulaw_enc')
  const [candidate, setCandidate] = useState('')
  const [testbench, setTestbench] = useState('')
  const [tbModule, setTbModule] = useState(MODULES[2])
  const [cycles, setCycles] = useState(200)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [wrapW, setWrapW] = useState(800)
  const [dpr, setDpr] = useState(() => window.devicePixelRatio || 1)

  const signals = useMemo(() => wave?.signals ?? [], [wave])
  const params = wave?.parameters ?? []
  const unit = wave?.time_unit ?? 'ps'

  const refreshLists = useCallback(() => {
    api.waveTests().then(setTests).catch(() => {})
    api.waveCaptures().then(setCaptures).catch(() => {})
  }, [])
  useEffect(() => { refreshLists() }, [refreshLists])

  const open = useCallback(async (name: string, scope?: string) => {
    setBusy('loading')
    const w = await traced(
      { stage: 'waveform', tool: 'vcd-reader', design: 'g711', cmd: `read_vcd build/waves/${name}.vcd${scope ? ` -scope ${scope}` : ''}` },
      () => api.wave(name, scope),
      (v) => {
        if (!v.available) { log.error('WAVE', v.error ?? 'unavailable'); return { status: 'fail' } }
        log.info('WAVE', `${v.signals?.length ?? 0} signals in ${v.scope}, ${v.transition_count} transitions, ${v.start}-${v.end}${v.time_unit}`)
        if (v.truncated) log.warn('WAVE', 'transition budget reached — window truncated')
        return { status: 'pass' }
      })
    setBusy(null)
    if (w) {
      setWave(w)
      if (w.available) {
        const start = w.start ?? 0
        setView({ start, span: Math.max(1, (w.end ?? 0) - start) })
        const errT = w.meta?.first_error?.match(/ t=(\d+)\s*$/)
        setCursor(errT ? Number(errT[1]) : start)
      }
    }
  }, [])

  const capture = useCallback(async (req: WaveRequest & { cycles?: number }) => {
    setBusy('capturing')
    const what = req.testbench ? `${req.testbench} (${req.module})` : `${req.test}${req.candidate ? ` on ${req.candidate}` : ''}`
    const c = await traced(
      { stage: 'simulate', tool: 'iverilog + $dumpvars window', design: 'g711', cmd: `waves.py ${what} --cycles ${req.cycles ?? cycles}` },
      () => api.waveCapture({ ...req, cycles: req.cycles ?? cycles }),
      (v) => {
        if (!v.available) { log.error('SIM', v.error ?? 'capture failed'); return { status: 'fail' } }
        ;(v.result?.startsWith('RESULT PASS') ? log.info : log.warn)('SIM', v.result ?? 'no RESULT line')
        if (v.first_error) log.error('SIM', v.first_error)
        log.note('WAVE', `window: clock edge ${v.window.start_cycle} + ${v.window.cycles} cycles — ${v.window.reason}; ${((v.size_bytes ?? 0) / 1024).toFixed(0)} KB`)
        return { status: 'pass' }
      })
    setBusy(null)
    refreshLists()
    if (c?.available) await open(c.name)
  }, [cycles, open, refreshLists])

  // A request from another view (e.g. "waveform" on a generated candidate) runs immediately.
  useEffect(() => {
    if (!request) return
    if (request.testbench) { setSource('testbench'); setTestbench(request.testbench); if (request.module) setTbModule(request.module) }
    else { setSource('gate'); if (request.test) setTest(request.test); setCandidate(request.candidate ?? '') }
    void capture(request)
  }, [request]) // eslint-disable-line react-hooks/exhaustive-deps

  function runSelected() {
    if (source === 'gate') void capture({ test, candidate: candidate.trim() || undefined })
    else void capture({ testbench: testbench.trim(), module: tbModule })
  }

  // ---- sizing ----
  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWrapW(e.contentRect.width))
    ro.observe(el)
    setWrapW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    // A `resolution` media query is the reliable way to hear about a zoom
    // change; it has to be re-armed each time because the query itself is
    // pinned to the ratio that was current when it was created.
    const onChange = () => setDpr(window.devicePixelRatio || 1)
    const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
    mq.addEventListener('change', onChange)
    window.addEventListener('resize', onChange)
    return () => {
      mq.removeEventListener('change', onChange)
      window.removeEventListener('resize', onChange)
    }
  }, [dpr])

  const NAME_W = nameWidth(wrapW)
  const VALUE_W = valueWidth(wrapW)
  const plotW = Math.max(120, wrapW - NAME_W - VALUE_W)
  const tToX = useCallback((t: number) => ((t - view.start) / view.span) * plotW, [view, plotW])
  const xToT = useCallback((x: number) => view.start + (x / plotW) * view.span, [view, plotW])

  // ---- drawing ----
  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || signals.length === 0) return
    const h = RULER_H + signals.length * ROW_H
    cv.width = plotW * dpr
    cv.height = h * dpr
    cv.style.width = `${plotW}px`
    cv.style.height = `${h}px`
    const g = cv.getContext('2d')
    if (!g) return
    g.setTransform(dpr, 0, 0, dpr, 0, 0)

    const root = cv
    const cBg = css(root, '--wave-bg', '#0d1219')
    const cGrid = css(root, '--wave-grid', '#1d2735')
    const cHi = css(root, '--wave-hi', '#4ade80')
    const cBus = css(root, '--wave-bus', '#60a5fa')
    const cX = css(root, '--wave-x', '#f87171')
    const cMuted = css(root, '--muted', '#8b9bb4')
    const cSel = css(root, '--accent', '#5b9dff')

    g.fillStyle = cBg
    g.fillRect(0, 0, plotW, h)

    // --- time ruler: pick a 1/2/5 x 10^n step so labels never collide ---
    const targetTicks = Math.max(2, Math.floor(plotW / 90))
    const raw = view.span / targetTicks
    const mag = Math.pow(10, Math.floor(Math.log10(raw)))
    const norm = raw / mag
    const step = (norm > 5 ? 10 : norm > 2 ? 5 : norm > 1 ? 2 : 1) * mag
    g.font = '10px ui-monospace, Menlo, monospace'
    g.textBaseline = 'middle'
    const first = Math.ceil(view.start / step) * step
    for (let t = first; t <= view.start + view.span; t += step) {
      const x = Math.round(tToX(t)) + 0.5
      g.strokeStyle = cGrid
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke()
      g.fillStyle = cMuted
      g.fillText(`${t}`, x + 3, RULER_H / 2)
    }
    g.strokeStyle = cGrid
    g.beginPath(); g.moveTo(0, RULER_H + 0.5); g.lineTo(plotW, RULER_H + 0.5); g.stroke()

    // --- signals ---
    signals.forEach((sig, i) => {
      const top = RULER_H + i * ROW_H
      const yHi = top + PAD
      const yLo = top + ROW_H - PAD
      const yMid = (yHi + yLo) / 2
      if (sig.name === selected) {
        g.fillStyle = 'rgba(91,157,255,0.08)'
        g.fillRect(0, top, plotW, ROW_H)
      }
      g.lineWidth = 1.4

      const tr = sig.transitions
      if (tr.length === 0) return
      // index of the last transition at or before the view start
      let i0 = 0
      while (i0 + 1 < tr.length && tr[i0 + 1][0] <= view.start) i0++

      if (sig.width === 1) {
        g.strokeStyle = cHi
        g.beginPath()
        let prevY = tr[i0][1] === '1' ? yHi : tr[i0][1] === '0' ? yLo : yMid
        let x = Math.max(0, tToX(tr[i0][0]))
        g.moveTo(x, prevY)
        for (let k = i0 + 1; k < tr.length; k++) {
          const [t, v] = tr[k]
          if (t > view.start + view.span) break
          const nx = tToX(t)
          g.lineTo(nx, prevY)
          const ny = v === '1' ? yHi : v === '0' ? yLo : yMid
          if (v === 'x' || v === 'z') { g.stroke(); g.strokeStyle = cX; g.beginPath(); g.moveTo(nx, prevY) }
          else if (g.strokeStyle === cX) { g.stroke(); g.strokeStyle = cHi; g.beginPath(); g.moveTo(nx, prevY) }
          g.lineTo(nx, ny)
          prevY = ny
          x = nx
        }
        g.lineTo(plotW, prevY)
        g.stroke()
      } else {
        // bus: hexagon segments, value centred when it fits
        g.font = '10px ui-monospace, Menlo, monospace'
        for (let k = i0; k < tr.length; k++) {
          const [t, v] = tr[k]
          if (t > view.start + view.span) break
          const x0 = Math.max(0, tToX(t))
          const next = k + 1 < tr.length ? tr[k + 1][0] : view.start + view.span
          const x1 = Math.min(plotW, tToX(next))
          if (x1 <= 0 || x1 - x0 < 0.4) continue
          const bad = /[xz]/.test(v)
          g.strokeStyle = bad ? cX : cBus
          const k2 = Math.min(3, (x1 - x0) / 2)
          g.beginPath()
          g.moveTo(x0, yMid); g.lineTo(x0 + k2, yHi); g.lineTo(x1 - k2, yHi)
          g.lineTo(x1, yMid); g.lineTo(x1 - k2, yLo); g.lineTo(x0 + k2, yLo)
          g.closePath(); g.stroke()
          const label = hex(v)
          if (x1 - x0 > label.length * 6 + 10) {
            g.fillStyle = bad ? cX : cBus
            g.fillText(label, (x0 + x1) / 2 - label.length * 3, yMid)
          }
        }
      }
    })

    // --- cursor ---
    const cx = Math.round(tToX(cursor)) + 0.5
    if (cx >= 0 && cx <= plotW) {
      g.strokeStyle = cSel
      g.setLineDash([3, 3])
      g.beginPath(); g.moveTo(cx, 0); g.lineTo(cx, h); g.stroke()
      g.setLineDash([])
    }
  }, [signals, view, plotW, cursor, selected, dpr])

  // ---- interaction ----
  function onCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    setCursor(Math.round(xToT(e.clientX - r.left)))
  }

  function onWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    if (!e.ctrlKey && !e.metaKey && !e.shiftKey) return
    e.preventDefault()
    const r = e.currentTarget.getBoundingClientRect()
    const anchor = xToT(e.clientX - r.left)
    const factor = e.deltaY > 0 ? 1.25 : 0.8
    const span = Math.max(1, Math.min(((wave?.end ?? 0) - (wave?.start ?? 0) || 1e6) * 4, view.span * factor))
    // keep the time under the pointer fixed while the span changes
    setView({ start: Math.max(0, anchor - (anchor - view.start) * (span / view.span)), span })
  }

  function zoom(factor: number) {
    const mid = view.start + view.span / 2
    const span = Math.max(1, Math.min(((wave?.end ?? 0) - (wave?.start ?? 0) || 1e6) * 4, view.span * factor))
    setView({ start: Math.max(0, mid - span / 2), span })
  }

  function fit() { const st = wave?.start ?? 0; setView({ start: st, span: Math.max(1, (wave?.end ?? 0) - st) }) }

  return (
    <div className="wave">
      <div className="pane-toolbar wave-source">
        <div className="tb-group">
          <button className={`tb-btn ${source === 'gate' ? 'tb-on' : ''}`} onClick={() => setSource('gate')}>gate testbench</button>
          <button className={`tb-btn ${source === 'testbench' ? 'tb-on' : ''}`} onClick={() => setSource('testbench')}>generated testbench</button>
        </div>
        {source === 'gate' ? (
          <div className="tb-group">
            <select className="fld-input" value={test} onChange={(e) => setTest(e.target.value)}>
              {tests.map((x) => <option key={x.name} value={x.name}>{x.name} (n={x.n}{x.bp ? '' : ', full-rate'})</option>)}
            </select>
            <input className="fld-input wave-path" value={candidate} onChange={(e) => setCandidate(e.target.value)}
                   placeholder="RTL: committed (or build/generate/<run>/iterN/<module>.sv)" />
          </div>
        ) : (
          <div className="tb-group">
            <select className="fld-input" value={tbModule} onChange={(e) => setTbModule(e.target.value)}>
              {MODULES.map((m) => <option key={m}>{m}</option>)}
            </select>
            <input className="fld-input wave-path" value={testbench} onChange={(e) => setTestbench(e.target.value)}
                   placeholder="build/generate_tb/<run>/iterN/tb_<module>.sv" />
          </div>
        )}
        <div className="tb-group">
          <span className="tb-label">cycles</span>
          <input className="fld-input wave-cycles" type="number" min={20} max={5000} value={cycles}
                 onChange={(e) => setCycles(Math.max(20, Math.min(5000, Number(e.target.value))))} />
          <button className="btn-run" onClick={runSelected} disabled={!!busy || (source === 'testbench' && !testbench.trim())}>
            {busy === 'capturing' ? '■ simulating…' : '▶ Run & trace'}
          </button>
        </div>
        <div className="tb-group tb-right">
          <select className="fld-input" value={wave?.name ?? ''} onChange={(e) => e.target.value && void open(e.target.value)}>
            <option value="">captures ({captures.length})…</option>
            {captures.map((c) => <option key={c.name} value={c.name}>{c.name}{c.first_error ? ' ✕' : ''}</option>)}
          </select>
        </div>
      </div>

      {!wave && <div className="pane-empty muted">Pick a testbench run and press ▶ Run &amp; trace, or open a previous capture.</div>}
      {wave && !wave.available && <div className="pane-empty"><p>{wave.error}</p></div>}
      {wave?.available && <>
      <div className="pane-toolbar">
        <div className="tb-group">
          <button className="tb-btn" onClick={() => zoom(0.5)} title={t('Zoom in')}>⊕</button>
          <button className="tb-btn" onClick={() => zoom(2)} title={t('Zoom out')}>⊖</button>
          <button className="tb-btn" onClick={fit} title={t('Zoom fit')}>⤢ Fit</button>
          <select className="fld-input" value={wave.scope} onChange={(e) => void open(wave.name!, e.target.value)}>
            {(wave.scopes ?? []).map((sc) => <option key={sc.name} value={sc.name}>{sc.name} ({sc.signals})</option>)}
          </select>
        </div>
        <div className="tb-group">
          <span className="tb-label">cursor</span>
          <span className="tb-readout">{cursor.toLocaleString()} {unit}</span>
          <span className="tb-label">window</span>
          <span className="tb-readout">{view.start.toLocaleString()} – {Math.round(view.start + view.span).toLocaleString()} {unit}</span>
        </div>
        <div className="tb-group tb-right">
          {wave.meta?.result && <span className={`pill ${wave.meta.result.startsWith('RESULT PASS') ? 'pill-ok' : 'pill-fail'}`}>{wave.meta.result}</span>}
          {wave.truncated && <span className="pill pill-warn">truncated</span>}
        </div>
      </div>
      {wave.meta?.first_error && <div className="wave-error mono small">✕ {wave.meta.first_error} — cursor placed at the mismatch</div>}
      <div className="wave-body" ref={wrapRef}>
        <div className="wave-names" style={{ width: NAME_W }}>
          <div className="wave-names-head" style={{ height: RULER_H }}>{t('signal')}</div>
          {signals.map((s) => (
            <button
              key={s.name}
              className={`wave-name ${selected === s.name ? 'wave-name-sel' : ''}`}
              style={{ height: ROW_H }}
              onClick={() => setSelected(s.name)}
              title={`${s.scope}.${s.name} [${s.width}]`}
            >
              <span className="wave-name-txt">{s.name}</span>
              {s.width > 1 && <span className="wave-name-w">[{s.width - 1}:0]</span>}
            </button>
          ))}
        </div>

        <canvas
          ref={canvasRef}
          className="wave-canvas"
          onClick={onCanvasClick}
          onWheel={onWheel}
          title="click to place cursor · ⌘/Ctrl+wheel to zoom"
        />

        <div className="wave-values" style={{ width: VALUE_W }}>
          <div className="wave-names-head" style={{ height: RULER_H }}>{t('value')}</div>
          {signals.map((s) => {
            const v = valueAt(s, cursor)
            const bad = /[xz]/.test(v)
            return (
              <div key={s.name} className={`wave-val ${bad ? 'wave-val-x' : ''}`} style={{ height: ROW_H }}>
                {s.width === 1 ? v : hex(v)}
              </div>
            )
          })}
        </div>
      </div>

      {params.length > 0 && (
        <div className="wave-params">
          <span className="tb-label">{t('parameters')}</span>
          {params.map((p) => (
            <span key={p.name} className="wave-param" title={`${p.name}[${p.width - 1}:0] = ${p.value}`}>
              <b>{p.name}</b>
              <span>{p.width > 1 ? hex(p.value) : p.value}</span>
            </span>
          ))}
        </div>
      )}

      <div className="pane-foot muted small">
        {signals.length} signals · scope <code>{wave?.scope}</code> · {wave?.transition_count?.toLocaleString()} transitions ·
        {wave?.meta ? ` ${wave.meta.window.reason} · ` : ' '}click to place cursor, ⌘/Ctrl+wheel to zoom
      </div>
      </>}
    </div>
  )
}
