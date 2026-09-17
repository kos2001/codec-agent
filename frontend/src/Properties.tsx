import type { NavSelection } from './Navigator'
import type { GenerateRunSummary, ModuleInfo, Proof, Requirement } from './types'
import { useT } from './i18n'

/** Object inspector — flat attribute list for whatever the navigator selected. */

function Row({ k, v, mono = true }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="prop-row">
      <span className="prop-k">{k}</span>
      <span className={`prop-v ${mono ? 'mono' : ''}`}>{v}</span>
    </div>
  )
}

export function Properties({ sel, gate, onOpen }: { sel: NavSelection | null; gate: Proof | null; onOpen: (view: string, arg?: string) => void }) {
  const t = useT()
  return (
    <aside className="properties">
      <div className="nav-title">{t('Properties')}</div>
      {!sel && <p className="muted small prop-empty">{t('Select an object in the navigator.')}</p>}
      {sel && <Body sel={sel} gate={gate} onOpen={onOpen} />}
    </aside>
  )
}

function Body({ sel, gate, onOpen }: { sel: NavSelection; gate: Proof | null; onOpen: (view: string, arg?: string) => void }) {
  if (sel.kind === 'module') {
    const m = sel.data as ModuleInfo
    return (
      <>
        <div className="prop-head">{m.name}</div>
        <Row k="object" v="module" mono={false} />
        <Row k="role" v={m.role} mono={false} />
        <Row k="source" v={`generated/g711/rtl/${m.name}.sv`} />
        <Row k="lines" v={m.lines} />
        <Row k="sha-256" v={gate?.rtl_sha256[m.name] ? <span title={gate.rtl_sha256[m.name]}>{gate.rtl_sha256[m.name].slice(0, 16)}…</span> : '—'} />
        <div className="prop-sep">requirements cited</div>
        <p className="prop-note small mono">{m.reqs.join(' ')}</p>
        <div className="prop-sep">port contract</div>
        <pre className="prop-pre">{m.ports}</pre>
        <div className="prop-actions">
          <button className="tb-btn" onClick={() => onOpen('rtl', m.name)}>open RTL</button>
          <button className="tb-btn" onClick={() => onOpen('generate', m.name)}>generate…</button>
        </div>
      </>
    )
  }
  if (sel.kind === 'req') {
    const r = sel.data as Requirement
    return (
      <>
        <div className="prop-head">{r.id}</div>
        <Row k="object" v="requirement" mono={false} />
        <Row k="section" v={r.section} />
        <Row k="text" v={r.text} mono={false} />
        <Row k="source" v={r.source ?? '—'} mono={false} />
        {r.design_choice && (
          <p className="prop-note prop-warn small">
            DESIGN choice: G.711 §3.6 leaves inputs on a decision value to the implementation. This follows the
            STL convention and is pinned by <code>test_boundary_convention_is_documented_not_asserted_by_spec</code>.
          </p>
        )}
      </>
    )
  }
  if (sel.kind === 'test') {
    const d = sel.data as { res: string; err?: string }
    return (
      <>
        <div className="prop-head">{sel.id}</div>
        <Row k="object" v="gate simulation test" mono={false} />
        <Row k="result" v={d.res} />
        {d.err && <Row k="first error" v={d.err} />}
        <p className="prop-note muted small">
          L1 = STL official vectors · L2 = exhaustive decode · L3 = table segment edges · P = full-rate
          throughput guard (BP=0, cycles ≤ N+3). All run with a hostile reset environment.
        </p>
      </>
    )
  }
  if (sel.kind === 'run') {
    const r = sel.data as GenerateRunSummary
    return (
      <>
        <div className="prop-head">{r.run_id}</div>
        <Row k="object" v="generation run" mono={false} />
        <Row k="module" v={r.module} />
        <Row k="outcome" v={<span className={`badge badge-${r.outcome === 'PROVEN' ? 'pass' : 'fail'}`}>{r.outcome}</span>} mono={false} />
        <Row k="iterations" v={r.iterations} />
        <Row k="≡ baseline" v={r.equivalent_to_baseline == null ? '—' : String(r.equivalent_to_baseline)} />
        <Row k="model" v={r.model} />
        <Row k="archive" v={`runs/${r.file}`} />
        <div className="prop-actions"><button className="tb-btn" onClick={() => onOpen('generate')}>open generate</button></div>
      </>
    )
  }
  return (
    <>
      <div className="prop-head">{sel.id}</div>
      <Row k="object" v="oracle" mono={false} />
      <Row k="role" v={String(sel.data)} mono={false} />
      <p className="prop-note muted small">
        Oracles are cross-checked against each other before any RTL is judged. If RTL and an oracle
        disagree, the RTL is wrong until shown otherwise.
      </p>
    </>
  )
}
