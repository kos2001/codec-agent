import { useEffect, useState } from 'react'
import { api } from './api'
import { useT } from './i18n'
import { renderMD } from './md'
import type { MutationRun, Proof, Requirements, TestRun } from './types'
import { Busy, useElapsed } from './ui'

export const STAGES = ['lint', 'synthesize', 'simulate', 'formal', 'equiv'] as const

export function StatusBadge({ status }: { status?: string }) {
  const cls = status === 'pass' ? 'pass' : status === 'fail' ? 'fail' : 'skip'
  return <span className={`badge badge-${cls}`}>{status ?? '—'}</span>
}

export function VerdictBar({ verdict, extra }: { verdict: string; extra?: string }) {
  const cls = verdict === 'PROVEN' ? 'SIGNED_OFF' : verdict === 'REJECTED' || verdict === 'ERRORED' ? 'REJECTED' : 'CONDITIONAL'
  return <div className={`verdict verdict-${cls}`}>VERDICT: {verdict}{extra ? ` · ${extra}` : ''}</div>
}

export function CodeView({ source, title }: { source: string; title?: string }) {
  return (
    <div className="code-view">
      {title && <div className="code-title mono">{title}</div>}
      <pre>
        {source.split('\n').map((l, i) => (
          <div key={i} className="code-line"><span className="code-ln">{i + 1}</span><span>{l}</span></div>
        ))}
      </pre>
    </div>
  )
}

// ---------------------------------------------------------------- sign-off ----
export function SignoffView({ proof, busy, onRun, onWave }: { proof: Proof | null; busy: boolean; onRun: () => void; onWave: (test: string) => void }) {
  const t = useT()
  const sec = useElapsed(busy)
  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>{t('Sign-off gate')}</h2>
          <button className="btn-run" onClick={onRun} disabled={busy}>
            {busy ? `■ ${t('Running gate…')}` : `▶ ${t('Run gate')}`}
          </button>
        </div>
        <p className="muted small">
          <code>pipeline/gate.py</code>: lint → synthesize → simulate (iverilog, 4-state) → formal
          (SymbiYosys, k-induction) → equiv (information only). Every stage runs; a gating skip makes the
          verdict INCONCLUSIVE.
        </p>
        {busy && <Busy label="gate" sec={sec} hint="~5 s" />}
        {proof && (
          <>
            <VerdictBar verdict={proof.verdict} extra={`${proof.name} · ${proof.elapsed_s}s${proof.file ? ` · ${proof.file}` : ''}`} />
            <div className="table-wrap">
              <table className="grid">
                <thead><tr><th>stage</th><th>status</th><th>gating</th><th>detail</th></tr></thead>
                <tbody>
                  {STAGES.map((s) => (
                    <tr key={s}>
                      <td className="mono">{s}</td>
                      <td><StatusBadge status={proof.stages[s]} /></td>
                      <td className="muted">{s === 'equiv' ? 'no' : 'yes'}</td>
                      <td className="muted small">{proof.details[s]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {proof && (
        <div className="panel">
          <div className="panel-head"><h2>{t('Simulation tests')}</h2>
            <span className="muted small">{Object.keys(proof.tests).length} tests · evidence = SHA-256 of expected vectors</span></div>
          <div className="table-wrap">
            <table className="grid">
              <thead><tr><th>test</th><th>result</th><th>expected-vector sha-256</th><th>first error</th><th></th></tr></thead>
              <tbody>
                {Object.entries(proof.tests).map(([name, res]) => {
                  const stem = name.startsWith('P_') ? undefined : name
                  const sha = stem ? proof.evidence_sha256[stem] : undefined
                  return (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td><StatusBadge status={res.startsWith('RESULT PASS') ? 'pass' : 'fail'} />
                        <span className="muted small"> {res.replace(/^RESULT \w+ /, '')}</span></td>
                      <td>{sha ? <span className="sha" title={sha}>{sha.slice(0, 12)}…</span> : <span className="sha sha-none">—</span>}</td>
                      <td className="mono small">{proof.first_errors[name] ?? ''}</td>
                      <td><button className="tb-btn" onClick={() => onWave(name)} title="run this test traced and open the waveform">◠ wave</button></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {proof && proof.verdict !== 'PROVEN' && proof.failure_evidence && (
        <div className="panel">
          <div className="panel-head"><h2>{t('Failure evidence')}</h2></div>
          <pre className="out-block">{proof.failure_evidence}</pre>
        </div>
      )}

      {proof && (
        <div className="panel panel-caveat">
          {proof.proves && <p><strong>{t('What PROVEN licenses you to say')}:</strong> {proof.proves}</p>}
          <p><strong>{t('What it does not prove')}:</strong> {proof.does_not_prove}</p>
        </div>
      )}
    </div>
  )
}

// -------------------------------------------------------------------- spec ----
export function SpecView({ reqs, tests, busy, onRunTests, onPick }: {
  reqs: Requirements | null; tests: TestRun | null; busy: boolean; onRunTests: () => void
  onPick: (id: string) => void
}) {
  const t = useT()
  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>{t('Requirements')}</h2>
          <span className="muted small">generated/g711/requirements.yaml · source = G.711 clause/table, or DESIGN where §3.6 leaves the choice open</span>
        </div>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>id</th><th>requirement</th><th>source</th></tr></thead>
            <tbody>
              {(reqs?.items ?? []).map((r) => (
                <tr key={r.id} className="row-pick" onClick={() => onPick(r.id)}>
                  <td className="mono">{r.id}</td>
                  <td className="small">{r.text}</td>
                  <td className="small">
                    {r.design_choice ? <span className="pill pill-warn">DESIGN</span>
                      : r.source ? <span className="muted">{r.source}</span> : <span className="muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {reqs && (
          <p className="panel-foot muted small">
            {t('Excluded from scope')}: {reqs.exclude.join(' · ')}
          </p>
        )}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{t('Oracle tests (L0)')}</h2>
          <button className="btn-run" onClick={onRunTests} disabled={busy}>{busy ? '■ …' : `▶ ${t('Run L0 tests')}`}</button>
        </div>
        <p className="muted small">
          <code>tests/test_golden.py</code> — spec_model vs ITU-T STL and table anchors ·{' '}
          <code>tests/test_spec_tables.py</code> — Tables 1a/1b/2a/2b rebuilt from the PDF's printed columns.
          Skipped = missing evidence (STL not built or PDF absent).
        </p>
        {tests && (
          <>
            <VerdictBar verdict={tests.returncode === 0 ? 'PROVEN' : 'REJECTED'} extra={`${tests.summary}`} />
            <div className="table-wrap">
              <table className="grid">
                <thead><tr><th>file</th><th>test</th><th>status</th><th>message</th></tr></thead>
                <tbody>
                  {tests.cases.map((c) => (
                    <tr key={`${c.file}-${c.name}`}>
                      <td className="mono small">{c.file}</td>
                      <td className="mono small">{c.name}</td>
                      <td><StatusBadge status={c.status} /></td>
                      <td className="muted small">{c.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- mutation ----
export function MutationView({ run, busy, onRun }: { run: MutationRun | null; busy: boolean; onRun: () => void }) {
  const t = useT()
  const sec = useElapsed(busy)
  const [filter, setFilter] = useState<'all' | 'survived' | 'formal-only'>('all')
  if (!run && !busy) {
    return <div className="pane-empty muted">No mutation campaign archived. <button onClick={onRun}>▶ Run</button></div>
  }
  const rows = (run?.results ?? []).filter((r) =>
    filter === 'all' ? true : filter === 'survived' ? r.status !== 'caught' : r.caught_by.length === 1 && r.caught_by[0] === 'formal')
  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>{t('Mutation coverage')}</h2>
          <button className="btn-run" onClick={onRun} disabled={busy}>{busy ? '■ running…' : '▶ Run campaign (~3 min)'}</button>
        </div>
        <p className="muted small">
          <code>pipeline/mutate.py</code>: one fault per mutant, the real gate with every stage on every
          mutant. Equivalent mutants are proven by <code>equiv</code> and excluded from the score.
        </p>
        {busy && <Busy label="mutation campaign" sec={sec} hint="8 workers" />}
        {run && (
          <>
            <div className="stat-strip">
              <div className={`stat ${run.survived ? 'stat-fail' : 'stat-ok'}`}>
                <div className="stat-value">{run.score_pct ?? '—'}%</div><div className="stat-label">detection</div></div>
              <div className="stat"><div className="stat-value">{run.caught}/{run.total - run.equivalent}</div><div className="stat-label">caught / non-equivalent</div></div>
              <div className="stat"><div className="stat-value">{run.equivalent}</div><div className="stat-label">equivalent</div></div>
              <div className="stat"><div className="stat-value">{run.elapsed_s}s</div><div className="stat-label">{run.file}</div></div>
            </div>
            <div className="flows-grid">
              <div>
                <h3 className="sub-h">{t('Detection by stage')}</h3>
                <table className="grid">
                  <thead><tr><th>stage</th><th className="num">caught</th><th className="num">only this stage</th></tr></thead>
                  <tbody>
                    {Object.entries(run.caught_by_stage).map(([s, n]) => (
                      <tr key={s}><td className="mono">{s}</td><td className="num">{n}</td><td className="num">{run.caught_only_by_stage[s] ?? 0}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <h3 className="sub-h">{t('By fault class')}</h3>
                <table className="grid">
                  <thead><tr><th>operator</th><th className="num">caught</th><th className="num">survived</th><th className="num">equiv</th></tr></thead>
                  <tbody>
                    {Object.entries(run.by_operator).map(([op, v]) => (
                      <tr key={op}><td className="mono">{op}</td><td className="num">{v.caught}</td>
                        <td className={`num ${v.survived ? 'sb-fail' : ''}`}>{v.survived}</td><td className="num">{v.equivalent}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>

      {run && (
        <div className="panel">
          <div className="panel-head">
            <h2>Mutants</h2>
            <div className="tb-group">
              {(['all', 'survived', 'formal-only'] as const).map((f) => (
                <button key={f} className={`tb-btn ${filter === f ? 'tb-on' : ''}`} onClick={() => setFilter(f)}>{f}</button>
              ))}
              <span className="muted small">{rows.length}</span>
            </div>
          </div>
          <div className="table-wrap table-tall">
            <table className="grid">
              <thead><tr><th>module</th><th>operator</th><th>line</th><th>change</th><th>status</th>
                {[['lint', 'lint'], ['synthesize', 'syn'], ['simulate', 'sim'], ['formal', 'fml']].map(([s, a]) => <th key={s} className="mx-h" title={s}>{a}</th>)}</tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.module}-${r.id}`}>
                    <td className="mono small">{r.module.replace('g711_', '')}</td>
                    <td className="mono small">{r.operator}</td>
                    <td className="num">{r.line}</td>
                    <td className="mono small mut-diff" title={`${r.before}\n→ ${r.after}`}>{r.after}</td>
                    <td><span className={`badge badge-${r.status === 'caught' ? 'pass' : r.status === 'survived' ? 'fail' : 'skip'}`}>{r.status}</span></td>
                    {['lint', 'synthesize', 'simulate', 'formal'].map((s) => (
                      <td key={s} className={`mx-cell mx-${r.stages[s] === 'fail' ? 'hit' : 'miss'}`}>{r.stages[s] === 'fail' ? '●' : '·'}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="panel-foot muted small">● = that stage rejected the mutant. A survivor is an edit every check accepts.</p>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- RTL view ----
export function RtlView({ module }: { module: string }) {
  const t = useT()
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => { setSrc(null); api.rtl(module).then((r) => setSrc(r.source)).catch(() => setSrc('// unavailable')) }, [module])
  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head"><h2>{t('Committed RTL')}</h2><span className="muted small mono">generated/g711/rtl/{module}.sv</span></div>
        {src === null ? <p className="muted">…</p> : <CodeView source={src} />}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------- docs ----
const DOCS: { id: string; label: string }[] = [
  { id: 'README', label: 'README' }, { id: 'PHASE0', label: 'Phase 0 report' },
  { id: 'PLAN', label: 'Service plan' }, { id: 'CLAUDE', label: 'CLAUDE.md (rules)' },
]

export function DocsView() {
  const t = useT()
  const [doc, setDoc] = useState('PHASE0')
  const [md, setMd] = useState<string>('')
  useEffect(() => { api.doc(doc).then((d) => setMd(d.markdown)).catch((e) => setMd(`unavailable: ${e}`)) }, [doc])
  return (
    <div className="view-scroll">
      <div className="panel">
        <div className="panel-head">
          <h2>{t('Documents')}</h2>
          <div className="tb-group">
            {DOCS.map((d) => (
              <button key={d.id} className={`tb-btn ${doc === d.id ? 'tb-on' : ''}`} onClick={() => setDoc(d.id)}>{d.label}</button>
            ))}
          </div>
        </div>
        <div className="md-doc">{renderMD(md)}</div>
      </div>
    </div>
  )
}
