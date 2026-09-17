import { useState } from 'react'
import { useT } from './i18n'
import type { GenerateRunSummary, ModuleInfo, Proof, Requirements } from './types'

/**
 * Design navigator — a browser, not a menu (adapted from hls-rtl): selecting a
 * node drives the properties pane; views are switched with the tabs.
 */

export interface NavSelection {
  kind: 'module' | 'req' | 'test' | 'run' | 'oracle'
  id: string
  data?: unknown
}

interface Props {
  modules: ModuleInfo[]
  reqs: Requirements | null
  gate: Proof | null
  runs: GenerateRunSummary[]
  selected: NavSelection | null
  onSelect: (s: NavSelection) => void
}

function Group({ label, count, children, defaultOpen = true }:
  { label: string; count?: number | string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="nav-group">
      <button className="nav-group-head" onClick={() => setOpen(!open)}>
        <span className="nav-caret">{open ? '▾' : '▸'}</span>
        <span className="nav-group-label">{label}</span>
        {count != null && <span className="nav-count">{count}</span>}
      </button>
      {open && <div className="nav-children">{children}</div>}
    </div>
  )
}

const ORACLES: [string, string][] = [
  ['g711.pdf', 'ITU-T G.711 (11/88) Tables 1a/1b/2a/2b'],
  ['stl/g711.c', 'ITU-T G.191 STL reference + sweep vectors'],
  ['spec_model.py', 'Python model from the table formulas'],
  ['g711_props.sv', 'formal reference functions + handshake invariants'],
]

export function Navigator({ modules, reqs, gate, runs, selected, onSelect }: Props) {
  const t = useT()
  const isSel = (kind: NavSelection['kind'], id: string) => selected?.kind === kind && selected.id === id
  const sections = ['interface', 'alaw', 'ulaw']

  return (
    <nav className="navigator">
      <div className="nav-title">{t('Design navigator')}</div>

      <Group label="g711 (RTL)" count={modules.length}>
        {modules.map((m) => (
          <button key={m.name} className={`nav-item ${isSel('module', m.name) ? 'nav-sel' : ''}`}
                  onClick={() => onSelect({ kind: 'module', id: m.name, data: m })}>
            <span className="nav-icon nav-icon-mod">M</span>{m.name}
          </button>
        ))}
      </Group>

      <Group label="Oracles" count={ORACLES.length}>
        {ORACLES.map(([id, role]) => (
          <button key={id} className={`nav-item ${isSel('oracle', id) ? 'nav-sel' : ''}`}
                  onClick={() => onSelect({ kind: 'oracle', id, data: role })}>
            <span className="nav-icon nav-icon-rep">O</span><span className="nav-trunc">{id}</span>
          </button>
        ))}
      </Group>

      <Group label="Requirements" count={reqs?.items.length ?? 0} defaultOpen={false}>
        {sections.map((s) => (
          <Group key={s} label={s} count={reqs?.items.filter((r) => r.section === s).length ?? 0} defaultOpen={false}>
            {(reqs?.items ?? []).filter((r) => r.section === s).map((r) => (
              <button key={r.id} className={`nav-item ${isSel('req', r.id) ? 'nav-sel' : ''}`}
                      onClick={() => onSelect({ kind: 'req', id: r.id, data: r })} title={r.text}>
                <span className={`nav-dot ${r.design_choice ? 'nav-dot-skip' : 'nav-dot-pass'}`} />{r.id}
              </button>
            ))}
          </Group>
        ))}
      </Group>

      <Group label="Gate tests" count={gate ? Object.keys(gate.tests).length : 0} defaultOpen={false}>
        {Object.entries(gate?.tests ?? {}).map(([name, res]) => (
          <button key={name} className={`nav-item ${isSel('test', name) ? 'nav-sel' : ''}`}
                  onClick={() => onSelect({ kind: 'test', id: name, data: { res, err: gate?.first_errors[name] } })}>
            <span className={`nav-dot nav-dot-${res.startsWith('RESULT PASS') ? 'pass' : 'fail'}`} />
            <span className="nav-trunc">{name}</span>
          </button>
        ))}
      </Group>

      <Group label="Generation runs" count={runs.length}>
        {runs.slice(0, 20).map((r) => (
          <button key={r.file} className={`nav-item ${isSel('run', r.file) ? 'nav-sel' : ''}`}
                  onClick={() => onSelect({ kind: 'run', id: r.file, data: r })}>
            <span className={`nav-dot nav-dot-${(r.outcome === 'PROVEN' || r.outcome === 'ACCEPTED') && r.regate !== 'REJECTED' ? 'pass' : 'fail'}`} />
            <span className="nav-trunc">{r.kind === 'testbench' ? 'tb:' : ''}{r.module.replace('g711_', '')} · {r.run_id.slice(9, 15)}{r.regate === 'REJECTED' ? ' (regate ✕)' : ''}</span>
          </button>
        ))}
      </Group>
    </nav>
  )
}
