import { useCallback, useEffect, useState } from 'react'
import { api, waitJob } from './api'
import { useT } from './i18n'
import { log } from './log'
import type { GenerateRun, GenerateRunSummary, Health, TbRun } from './types'
import { Busy, useElapsed } from './ui'
import { CodeView, STAGES, StatusBadge, VerdictBar } from './Views'
import type { WaveRequest } from './Waveform'

/**
 * Generation loop — Hermes proposes, the gate decides (adapted from hls-rtl Improve).
 *
 * Every attempt is shown, including the rejected ones and the evidence that was fed
 * back: a loop that only reports its successes teaches nothing about whether it works.
 */

const MODULES = ['g711_alaw_enc', 'g711_alaw_dec', 'g711_ulaw_enc', 'g711_ulaw_dec']

export function GeneratePane({ health, onRunsChanged, onWave }: { health: Health | null; onRunsChanged: () => void; onWave: (r: WaveRequest) => void }) {
  const [kind, setKind] = useState<'rtl' | 'testbench'>('rtl')
  return (
    <>
      <div className="pane-toolbar">
        <button className={`tb-btn ${kind === 'rtl' ? 'tb-on' : ''}`} onClick={() => setKind('rtl')}>RTL generation</button>
        <button className={`tb-btn ${kind === 'testbench' ? 'tb-on' : ''}`} onClick={() => setKind('testbench')}>Testbench generation</button>
      </div>
      {kind === 'rtl'
        ? <RtlGenerate health={health} onRunsChanged={onRunsChanged} onWave={onWave} />
        : <TbGenerate health={health} onRunsChanged={onRunsChanged} onWave={onWave} />}
    </>
  )
}

function RtlGenerate({ health, onRunsChanged, onWave }: { health: Health | null; onRunsChanged: () => void; onWave: (r: WaveRequest) => void }) {
  const t = useT()
  const [module, setModule] = useState(MODULES[2])
  const [iters, setIters] = useState(3)
  const [busy, setBusy] = useState(false)
  const sec = useElapsed(busy)
  const [runs, setRuns] = useState<GenerateRunSummary[]>([])
  const [run, setRun] = useState<GenerateRun | null>(null)
  const [prompt, setPrompt] = useState<string | null>(null)
  const [code, setCode] = useState<{ path: string; source: string } | null>(null)

  const loadRuns = useCallback(() => api.generateRuns().then((r) => setRuns(r.filter((x) => x.kind !== 'testbench'))).catch(() => setRuns([])), [])
  useEffect(() => { void loadRuns() }, [loadRuns])

  const ready = !!health?.hermes.model_available

  async function start() {
    setBusy(true); setRun(null); setCode(null)
    log.cmd(`generate.py --module ${module} --max-iters ${iters}`)
    log.banner('generate', `hermes ${health?.hermes_model ?? ''}`, module)
    const t0 = performance.now()
    try {
      const job = await api.generate(module, iters)
      const done = await waitJob<GenerateRun>(job.id)
      if (done.status === 'error' || !done.result) throw new Error(done.error ?? 'job failed')
      const r = done.result
      for (const a of r.attempts) {
        if (a.error) log.error('GEN', `iter${a.iteration}: ${a.error}`)
        else {
          const f = a.verdict === 'PROVEN' ? log.info : log.warn
          f('GEN', `iter${a.iteration}: ${a.verdict} — ${STAGES.map((s) => `${s}=${a.stages?.[s] ?? '-'}`).join(' ')} · ${a.usage?.total_tokens ?? '?'} tok · llm ${a.llm_elapsed_s}s`)
          if (a.evidence) log.note('GEN', `evidence fed back to iter${a.iteration + 1}`, a.evidence)
        }
      }
      log.verdict('generate', r.outcome === 'PROVEN' ? 'pass' : 'fail', performance.now() - t0,
        `${r.outcome} after ${r.iterations} iteration(s)${r.equivalent_to_baseline ? ' · ≡ baseline' : ''}`)
      setRun(r)
      if (r.proven_candidate) void showCode(r.proven_candidate)
    } catch (e) {
      log.error('GEN', `${module}: ${e instanceof Error ? e.message : String(e)}`)
      log.verdict('generate', 'fail', performance.now() - t0)
    }
    setBusy(false)
    void loadRuns(); onRunsChanged()
  }

  async function open(file: string) {
    setCode(null)
    const r = (await api.generateRun(file)) as GenerateRun
    setRun(r); setModule(r.module)
    const last = r.proven_candidate ?? [...r.attempts].reverse().find((a) => a.candidate)?.candidate
    if (last) void showCode(last)
  }

  async function showCode(path: string) {
    try { setCode(await api.candidate(path)) } catch { setCode({ path, source: '// candidate file no longer exists (build/ was cleaned)' }) }
  }

  async function regate(path: string) {
    log.cmd(`gate.py --rtl-dir ${path.replace(/\/[^/]+$/, '')}`)
    const t0 = performance.now()
    try {
      const p = await api.gate(path)
      log.verdict('gate', p.verdict === 'PROVEN' ? 'pass' : 'fail', performance.now() - t0, `${p.verdict} (re-run on candidate)`)
    } catch (e) {
      log.error('GATE', String(e)); log.verdict('gate', 'fail', performance.now() - t0)
    }
  }

  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>{t('Generation loop (Hermes)')}</h2>
          <span className={`pill ${ready ? 'pill-ok' : 'pill-warn'}`}>
            {ready ? `${health?.hermes.model} → ${health?.hermes_model}` : health?.hermes.error ?? 'hermes not ready'}
          </span>
        </div>
        <p className="muted small">
          <code>pipeline/generate.py</code>: requirements + port contract only (never the committed
          implementation) → Hermes → <code>gate.prove</code>. On REJECTED the gate's failure evidence and the
          rejected code go back for repair, up to the iteration budget. Candidates are never applied automatically.
        </p>
        <div className="gen-controls">
          <label className="fld-label">module
            <select className="fld-input" value={module} onChange={(e) => setModule(e.target.value)} disabled={busy}>
              {MODULES.map((m) => <option key={m}>{m}</option>)}
            </select>
          </label>
          <label className="fld-label">max iterations
            <input className="fld-input" type="number" min={1} max={6} value={iters}
                   onChange={(e) => setIters(Math.max(1, Math.min(6, Number(e.target.value))))} disabled={busy} />
          </label>
          <button className="btn-run" onClick={start} disabled={busy || !ready}>{busy ? '■ generating…' : '▶ Generate & prove'}</button>
          <button className="tb-btn" onClick={() => api.generatePrompt(module).then((p) => setPrompt(p.prompt))}>
            {t('Prompt sent to the model')}
          </button>
        </div>
        {busy && <Busy label={`hermes → gate (${module})`} sec={sec} hint="LLM call + ~5 s gate per iteration" />}
        {prompt && !busy && (
          <details className="gen-prompt" open>
            <summary className="muted small">{t('Prompt sent to the model')} ({prompt.length} chars)</summary>
            <pre className="out-block">{prompt}</pre>
          </details>
        )}
      </div>

      {run && (
        <div className="panel">
          <div className="panel-head">
            <h2>{t('Attempts')} — {run.module}</h2>
            <span className="muted small mono">{run.run_id} · {run.model}</span>
          </div>
          <VerdictBar verdict={run.outcome === 'PROVEN' ? 'PROVEN' : run.outcome === 'ERRORED' ? 'ERRORED' : 'INCONCLUSIVE'}
            extra={`${run.outcome} · ${run.iterations}/${run.max_iters} iterations${run.equivalent_to_baseline ? ' · logically ≡ committed RTL' : run.outcome === 'PROVEN' ? ' · differs from committed RTL' : ''}`} />
          <div className="attempts">
            {run.attempts.map((a) => (
              <div key={a.iteration} className={`attempt attempt-${a.verdict ?? 'ERROR'}`}>
                <div className="attempt-head">
                  <span className="step-num">{a.iteration}</span>
                  <span className="mono">{a.verdict ?? 'ERROR'}</span>
                  {STAGES.map((s) => a.stages?.[s] && <span key={s} className="attempt-stage"><span className="muted small">{s}</span> <StatusBadge status={a.stages[s]} /></span>)}
                  <span className="muted small attempt-meta">
                    {a.usage?.total_tokens ? `${a.usage.prompt_tokens}+${a.usage.completion_tokens} tok · ` : ''}
                    {a.llm_elapsed_s != null ? `llm ${a.llm_elapsed_s}s` : ''}{a.gate_elapsed_s != null ? ` · gate ${a.gate_elapsed_s}s` : ''}
                  </span>
                  {a.candidate && <>
                    <button className="tb-btn" onClick={() => showCode(a.candidate!)}>code</button>
                    <button className="tb-btn" onClick={() => regate(a.candidate!)}>re-gate</button>
                    <button className="tb-btn" title="trace the first failing (or the module's L1) test on this candidate"
                      onClick={() => onWave({ candidate: a.candidate!, test: firstFailingTest(a.details?.simulate, run.module) })}>◠ wave</button>
                  </>}
                </div>
                {a.error && <pre className="out-block">{a.error}{a.reply_head ? `\n---\n${a.reply_head}` : ''}</pre>}
                {a.evidence && (
                  <details><summary className="muted small">evidence fed back to the next attempt</summary>
                    <pre className="out-block">{a.evidence}</pre></details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {code && (
        <div className="panel">
          <div className="panel-head"><h2>{t('Candidate RTL')}</h2><span className="muted small mono">{code.path}</span></div>
          <CodeView source={code.source} />
        </div>
      )}

      <div className="panel">
        <div className="panel-head"><h2>{t('Run history')}</h2><button className="tb-btn" onClick={() => void loadRuns()}>↻</button></div>
        {runs.length === 0 ? <p className="muted small">No generation runs archived in runs/.</p> : (
          <div className="table-wrap">
            <table className="grid">
              <thead><tr><th>run</th><th>module</th><th>outcome</th><th className="num">iters</th><th>≡ baseline</th><th>model</th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.file} className="row-pick" onClick={() => void open(r.file)}>
                    <td className="mono small">{r.run_id.slice(0, 15)}</td>
                    <td className="mono small">{r.module}</td>
                    <td><span className={`badge badge-${r.outcome === 'PROVEN' && r.regate !== 'REJECTED' ? 'pass' : 'fail'}`}>{r.outcome}{r.regate === 'REJECTED' ? ' → REJECTED' : ''}</span></td>
                    <td className="num">{r.iterations}</td>
                    <td className="muted small">{r.equivalent_to_baseline == null ? '—' : r.equivalent_to_baseline ? 'yes' : 'no'}</td>
                    <td className="muted small">{r.model}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// --------------------------------------------------------------- testbench ----
function TbGenerate({ health, onRunsChanged, onWave }: { health: Health | null; onRunsChanged: () => void; onWave: (r: WaveRequest) => void }) {
  const [module, setModule] = useState(MODULES[2])
  const [iters, setIters] = useState(3)
  const [minKill, setMinKill] = useState(90)
  const [busy, setBusy] = useState(false)
  const sec = useElapsed(busy)
  const [runs, setRuns] = useState<GenerateRunSummary[]>([])
  const [run, setRun] = useState<TbRun | null>(null)
  const [code, setCode] = useState<{ path: string; source: string } | null>(null)
  const [prompt, setPrompt] = useState<string | null>(null)
  const ready = !!health?.hermes.model_available

  const loadRuns = useCallback(() => api.generateRuns().then((r) => setRuns(r.filter((x) => x.kind === 'testbench'))).catch(() => setRuns([])), [])
  useEffect(() => { void loadRuns() }, [loadRuns])

  async function showCode(path: string) {
    try { setCode(await api.candidate(path)) } catch { setCode({ path, source: '// testbench file no longer exists (build/ was cleaned)' }) }
  }

  async function start() {
    setBusy(true); setRun(null); setCode(null)
    log.cmd(`generate_tb.py --module ${module} --max-iters ${iters} --min-kill ${minKill}`)
    log.banner('generate-tb', `hermes ${health?.hermes_model ?? ''} + iverilog`, module)
    const t0 = performance.now()
    try {
      const job = await api.generateTb(module, iters, minKill)
      const done = await waitJob<TbRun>(job.id)
      if (done.status === 'error' || !done.result) throw new Error(done.error ?? 'job failed')
      const r = done.result
      for (const a of r.attempts) {
        if (a.error) { log.error('TBGEN', `iter${a.iteration}: ${a.error}`); continue }
        const f = a.verdict === 'ACCEPTED' ? log.info : log.warn
        f('TBGEN', `iter${a.iteration}: ${a.verdict} — checks=${a.soundness?.checks ?? '-'}` +
          (a.kill ? ` · killed ${a.kill.killed}/${a.kill.considered} mutants (${a.kill.kill_pct}%)` : ''))
        if (a.evidence) log.note('TBGEN', `soundness evidence fed back to iter${a.iteration + 1}`, a.evidence)
      }
      log.verdict('generate-tb', r.outcome === 'ACCEPTED' ? 'pass' : 'fail', performance.now() - t0,
        `${r.outcome}${r.kill_pct != null ? ` · kill ${r.kill_pct}%` : ''}`)
      setRun(r)
      if (r.testbench) void showCode(r.testbench)
    } catch (e) {
      log.error('TBGEN', `${module}: ${e instanceof Error ? e.message : String(e)}`)
      log.verdict('generate-tb', 'fail', performance.now() - t0)
    }
    setBusy(false); void loadRuns(); onRunsChanged()
  }

  async function open(file: string) {
    setCode(null)
    const r = (await api.generateRun(file)) as TbRun
    setRun(r); setModule(r.module)
    const last = r.testbench ?? [...r.attempts].reverse().find((a) => a.testbench)?.testbench
    if (last) void showCode(last)
  }

  const verdictOf = (o: TbRun['outcome']) => (o === 'ACCEPTED' ? 'PROVEN' : o === 'ERRORED' ? 'ERRORED' : 'INCONCLUSIVE')

  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>Testbench generation (Hermes)</h2>
          <span className={`pill ${ready ? 'pill-ok' : 'pill-warn'}`}>{ready ? health?.hermes_model : 'hermes not ready'}</span>
        </div>
        <p className="muted small">
          <code>pipeline/generate_tb.py</code>: the model writes a self-checking testbench with its <b>own</b> reference
          model (no expected vectors, no RTL). Tools then judge the testbench: <b>soundness</b> — it must PASS on the
          committed RTL the gate has PROVEN; <b>sensitivity</b> — it must FAIL on the module's mutants (kill rate).
          An always-PASS testbench scores 0%.
        </p>
        <div className="gen-controls">
          <label className="fld-label">module
            <select className="fld-input" value={module} onChange={(e) => setModule(e.target.value)} disabled={busy}>
              {MODULES.map((m) => <option key={m}>{m}</option>)}
            </select>
          </label>
          <label className="fld-label">max iterations
            <input className="fld-input" type="number" min={1} max={6} value={iters}
                   onChange={(e) => setIters(Math.max(1, Math.min(6, Number(e.target.value))))} disabled={busy} />
          </label>
          <label className="fld-label">min kill %
            <input className="fld-input" type="number" min={0} max={100} value={minKill}
                   onChange={(e) => setMinKill(Math.max(0, Math.min(100, Number(e.target.value))))} disabled={busy} />
          </label>
          <button className="btn-run" onClick={start} disabled={busy || !ready}>{busy ? '■ generating…' : '▶ Generate & judge'}</button>
          <button className="tb-btn" onClick={() => api.generatePrompt(module).then((p) => setPrompt(p.tb_prompt))}>Prompt</button>
        </div>
        {busy && <Busy label={`hermes → iverilog (${module})`} sec={sec} hint="LLM call + soundness run + mutant sweep" />}
        {prompt && !busy && (
          <details className="gen-prompt" open>
            <summary className="muted small">testbench prompt ({prompt.length} chars)</summary>
            <pre className="out-block">{prompt}</pre>
          </details>
        )}
      </div>

      {run && (
        <div className="panel">
          <div className="panel-head"><h2>Attempts — {run.module}</h2><span className="muted small mono">{run.run_id} · {run.model}</span></div>
          <VerdictBar verdict={verdictOf(run.outcome)}
            extra={`${run.outcome} · ${run.iterations}/${run.max_iters} iterations${run.kill_pct != null ? ` · kill ${run.kill_pct}% (min ${run.min_kill_pct}%)` : ''}`} />
          <div className="attempts">
            {run.attempts.map((a) => (
              <div key={a.iteration} className={`attempt attempt-${a.verdict === 'ACCEPTED' ? 'PROVEN' : a.verdict === 'WEAK' ? 'INCONCLUSIVE' : 'REJECTED'}`}>
                <div className="attempt-head">
                  <span className="step-num">{a.iteration}</span>
                  <span className="mono">{a.verdict ?? 'ERROR'}</span>
                  {a.soundness && <span className="attempt-stage"><span className="muted small">sound</span>
                    <StatusBadge status={a.sound ? 'pass' : 'fail'} /></span>}
                  {a.soundness?.checks != null && <span className="muted small">{a.soundness.checks.toLocaleString()} checks</span>}
                  {a.kill && <span className="attempt-stage"><span className="muted small">mutants killed</span>
                    <span className={`badge badge-${(a.kill.kill_pct ?? 0) >= run.min_kill_pct ? 'pass' : 'fail'}`}>
                      {a.kill.killed}/{a.kill.considered} · {a.kill.kill_pct}%</span></span>}
                  <span className="muted small attempt-meta">
                    {a.usage?.total_tokens ? `${a.usage.prompt_tokens}+${a.usage.completion_tokens} tok · ` : ''}
                    {a.llm_elapsed_s != null ? `llm ${a.llm_elapsed_s}s` : ''}{a.check_elapsed_s != null ? ` · check ${a.check_elapsed_s}s` : ''}
                  </span>
                  {a.testbench && <button className="tb-btn" onClick={() => showCode(a.testbench!)}>code</button>}
                  {a.testbench && <button className="tb-btn" onClick={() => onWave({ testbench: a.testbench!, module: run.module })}>◠ wave</button>}
                </div>
                {a.error && <pre className="out-block">{a.error}{a.reply_head ? `\n---\n${a.reply_head}` : ''}</pre>}
                {a.evidence && <details><summary className="muted small">soundness evidence fed back</summary><pre className="out-block">{a.evidence}</pre></details>}
                {a.kill && a.kill.survivors.length > 0 && (
                  <details><summary className="muted small">{a.kill.survivors.length} surviving mutant(s) — faults this testbench misses</summary>
                    <pre className="out-block">{a.kill.survivors.map((m) => `${m.operator} L${m.line}: ${m.after}`).join('\n')}</pre></details>
                )}
              </div>
            ))}
          </div>
          <p className="panel-foot muted small">{run.note}</p>
        </div>
      )}

      {code && (
        <div className="panel">
          <div className="panel-head"><h2>Generated testbench</h2><span className="muted small mono">{code.path}</span></div>
          <CodeView source={code.source} />
        </div>
      )}

      <div className="panel">
        <div className="panel-head"><h2>Testbench run history</h2><button className="tb-btn" onClick={() => void loadRuns()}>↻</button></div>
        {runs.length === 0 ? <p className="muted small">No testbench generation runs archived.</p> : (
          <div className="table-wrap">
            <table className="grid">
              <thead><tr><th>run</th><th>module</th><th>outcome</th><th className="num">iters</th><th className="num">kill %</th><th>model</th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.file} className="row-pick" onClick={() => void open(r.file)}>
                    <td className="mono small">{r.run_id.slice(0, 15)}</td>
                    <td className="mono small">{r.module}</td>
                    <td><span className={`badge badge-${r.outcome === ('ACCEPTED' as string) ? 'pass' : 'fail'}`}>{r.outcome}</span></td>
                    <td className="num">{r.iterations}</td>
                    <td className="num">{r.kill_pct ?? '—'}</td>
                    <td className="muted small">{r.model}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

/** The gate test to trace for a candidate: L1 test of its own module (the gate's
 *  per-test failures are not in the summary, and L1 covers every input). */
function firstFailingTest(_simDetail: string | undefined, module: string): string {
  const law = module.includes('alaw') ? 'alaw' : 'ulaw'
  return module.endsWith('_enc') ? `L1_${law}_enc` : `L1_${law}_dec`
}
