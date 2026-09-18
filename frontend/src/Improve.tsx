import { useCallback, useEffect, useState } from 'react'
import { api, waitJob } from './api'
import { log, traced } from './log'
import type { ImproveReport, ImproveRound, KbHit, KbStats } from './types'
import { Busy, useElapsed } from './ui'

/**
 * Self-improvement loop (pipeline/improve.py) — measure, rank what is MISSING, act, re-measure.
 * Adapted from lsi_error_analyzer's L1/L2 split: the read-only report is safe to run any time;
 * an action re-measures afterwards and a regression gate says when a metric got worse.
 */
export function ImprovePane() {
  const [rep, setRep] = useState<ImproveReport | null>(null)
  const [kb, setKb] = useState<KbStats | null>(null)
  const [hits, setHits] = useState<KbHit[] | null>(null)
  const [q, setQ] = useState('lint FAILED Operator ASSIGN expects 4 bits')
  const [busy, setBusy] = useState<string | null>(null)
  const sec = useElapsed(!!busy)

  const load = useCallback(async () => {
    setBusy('measure')
    const r = await traced(
      { stage: 'self-improve', tool: 'gate+pytest+mutate+kb', design: 'g711', cmd: 'improve.py report' },
      () => api.improveReport(),
      (v) => {
        for (const rec of v.recommendations) {
          (rec.priority <= 1 ? log.error : rec.priority <= 2 ? log.warn : log.note)('IMPROVE', `${rec.id}: ${rec.why}`)
        }
        if (!v.recommendations.length) log.info('IMPROVE', 'no measured gap is open')
        return { status: v.recommendations.some((x) => x.priority <= 1) ? 'fail' : 'pass' }
      })
    if (r) setRep(r)
    api.kbStats().then(setKb).catch(() => {})
    setBusy(null)
  }, [])
  useEffect(() => { void load() }, [load])

  async function act(action: string) {
    setBusy(action)
    const t0 = performance.now()
    log.cmd(`improve.py act --action ${action}`)
    try {
      const job = await api.improveAct(action)
      const done = await waitJob<ImproveRound>(job.id, undefined, 3000)
      if (done.status !== 'done' || !done.result) throw new Error(done.error ?? 'job failed')
      const d = done.result.delta
      for (const [k, v] of Object.entries(d?.better ?? {})) log.info('IMPROVE', `${k}: ${v[0]} → ${v[1]}`)
      for (const [k, v] of Object.entries(d?.worse ?? {})) log.error('IMPROVE', `regression ${k}: ${v[0]} → ${v[1]}`)
      log.verdict('self-improve', d?.regressed ? 'fail' : 'pass', performance.now() - t0,
        d?.regressed ? 'regression gate tripped' : 'no metric got worse')
    } catch (e) {
      log.error('IMPROVE', String(e))
      log.verdict('self-improve', 'fail', performance.now() - t0)
    }
    setBusy(null)
    void load()
  }

  async function search() {
    const r = await api.kbSearch(q, 3).catch(() => null)
    setHits(r?.hits ?? [])
    log.info('KB', `${r?.hits.length ?? 0} case(s) retrieved for "${q.slice(0, 48)}"`)
  }

  const m = rep?.metrics ?? {}
  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>Self-improvement loop</h2>
          <button className="btn-run" onClick={load} disabled={!!busy}>{busy === 'measure' ? '■ measuring…' : '↻ Measure'}</button>
        </div>
        <p className="muted small">
          <code>pipeline/improve.py</code>: measure → rank the gaps → act → re-measure → regression gate.
          Every number comes from the gate, pytest, the mutation campaign or the KB evaluation; the loop
          decides nothing by itself.
        </p>
        {busy === 'measure' && <Busy label="running the gate, L0 tests and the KB evaluation" sec={sec} />}
        <div className="stat-strip">
          {Object.entries(m).map(([k, v]) => (
            <div key={k} className={`stat ${typeof v === 'number' && v < 0 ? 'stat-fail' : ''}`}>
              <div className="stat-value">{String(v)}</div><div className="stat-label">{k}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h2>Ranked gaps</h2>
          <span className="muted small">{rep?.recommendations.length ?? 0} open</span></div>
        {rep && rep.recommendations.length === 0 && <p className="muted small">No measured gap is open.</p>}
        <div className="attempts">
          {(rep?.recommendations ?? []).map((r) => (
            <div key={r.id} className={`attempt attempt-${r.priority <= 1 ? 'REJECTED' : r.priority <= 2 ? 'INCONCLUSIVE' : 'PROVEN'}`}>
              <div className="attempt-head">
                <span className="step-num">P{r.priority}</span>
                <span className="mono">{r.id}</span>
                <span className="muted small attempt-meta">{r.action ? `action: ${r.action}` : 'manual'}</span>
                {r.action && <button className="tb-btn" onClick={() => act(r.action!)} disabled={!!busy}>▶ act</button>}
              </div>
              <div className="small muted">{r.why}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h2>Knowledge base</h2>
          <span className="muted small">{kb ? `${kb.cases} cases · ${JSON.stringify(kb.by_kind)}` : '—'}</span></div>
        <p className="muted small">
          Cases are past failures with the edit that fixed them, mined from <code>runs/</code> plus the rules in
          CLAUDE.md. Retrieval is BM25 + entity overlap with a coverage gate (adapted from lsi_error_analyzer).
        </p>
        <div className="gen-controls">
          <input className="fld-input wave-path" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="paste a failure message" />
          <button className="btn-run" onClick={search}>▶ Search</button>
        </div>
        {hits && hits.length === 0 && <p className="muted small">No case passed the coverage gate — better no context than a weak match.</p>}
        {(hits ?? []).map((h) => (
          <div key={h.key} className="attempt">
            <div className="attempt-head">
              <span className="mono">{h.key}</span>
              <span className="muted small">{h.kind} · {h.module} · {h.stage}</span>
              <span className="muted small attempt-meta">bm25 {h.bm25} · entities {h.overlap} · {h.source}</span>
            </div>
            <pre className="out-block">{`SYMPTOM: ${h.symptom}\n\nFIX:\n${h.resolution}`}</pre>
          </div>
        ))}
      </div>

      {rep && rep.history.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h2>History</h2></div>
          <div className="table-wrap">
            <table className="grid">
              <thead><tr><th>at</th><th>action</th><th>rc</th><th>elapsed</th><th>better</th><th>worse</th></tr></thead>
              <tbody>
                {rep.history.slice().reverse().map((h, i) => (
                  <tr key={i}>
                    <td className="mono small">{h.at}</td>
                    <td className="mono small">{h.recommendation?.action ?? '—'}</td>
                    <td className="num">{h.result?.rc}</td>
                    <td className="num">{h.result?.elapsed_s}s</td>
                    <td className="small">{Object.keys(h.delta?.better ?? {}).join(', ') || '—'}</td>
                    <td className={`small ${h.delta?.regressed ? 'sb-fail' : ''}`}>{Object.keys(h.delta?.worse ?? {}).join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
