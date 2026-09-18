import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, waitJob } from './api'
import { Console } from './Console'
import { GeneratePane } from './Generate'
import { LangContext, readStoredLang, storeLang, useLang, useT } from './i18n'
import type { Lang } from './i18n'
import { log, traced } from './log'
import { Navigator } from './Navigator'
import type { NavSelection } from './Navigator'
import { Overview } from './Overview'
import { Properties } from './Properties'
import { Splitter, applySavedPanes, resetPanes } from './Splitter'
import type { GenerateRunSummary, Health, ModuleInfo, MutationRun, Proof, Requirements, TestRun } from './types'
import { DocsView, MutationView, RtlView, SignoffView, SpecView } from './Views'
import { ImprovePane } from './Improve'
import { WaveformPane } from './Waveform'
import type { WaveRequest } from './Waveform'

/**
 * Workbench shell adapted from hls-rtl: menu bar, run toolbar, design navigator,
 * tabbed work area, properties inspector, tool transcript, status bar. Every flow
 * writes severity-tagged lines into one transcript.
 */

type View = 'overview' | 'signoff' | 'spec' | 'mutation' | 'generate' | 'waveform' | 'improve' | 'rtl' | 'docs'

const VIEWS: { id: View; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'signoff', label: 'Sign-off' },
  { id: 'spec', label: 'Spec & Oracles' },
  { id: 'mutation', label: 'Mutation' },
  { id: 'generate', label: 'Generate' },
  { id: 'waveform', label: 'Waveform' },
  { id: 'improve', label: 'Improve' },
  { id: 'rtl', label: 'RTL' },
  { id: 'docs', label: 'Docs' },
]

function Menu({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])
  return (
    <div className="menu" ref={ref}>
      <button className={`menu-btn ${open ? 'menu-btn-open' : ''}`} onClick={() => setOpen(!open)}>{label}</button>
      {open && <div className="menu-pop" onClick={() => setOpen(false)}>{children}</div>}
    </div>
  )
}

function MenuItem({ onClick, children, accel, disabled }:
  { onClick: () => void; children: React.ReactNode; accel?: string; disabled?: boolean }) {
  return (
    <button className="menu-item" onClick={onClick} disabled={disabled}>
      <span>{children}</span>{accel && <span className="menu-accel">{accel}</span>}
    </button>
  )
}

export default function App() {
  const [lang, setLangState] = useState<Lang>(readStoredLang)
  const setLang = useCallback((l: Lang) => { setLangState(l); storeLang(l) }, [])
  useEffect(() => { document.documentElement.lang = lang }, [lang])
  return (
    <LangContext.Provider value={{ lang, setLang }}>
      <Workbench />
    </LangContext.Provider>
  )
}

function Workbench() {
  const t = useT()
  const { lang, setLang } = useLang()
  const [view, setView] = useState<View>('overview')
  const [rtlModule, setRtlModule] = useState('g711_ulaw_enc')
  const [waveReq, setWaveReq] = useState<WaveRequest | null>(null)
  const showWave = (r: WaveRequest) => { setWaveReq({ ...r }); setView('waveform') }
  const [health, setHealth] = useState<Health | null>(null)
  const [modules, setModules] = useState<ModuleInfo[]>([])
  const [reqs, setReqs] = useState<Requirements | null>(null)
  const [gate, setGate] = useState<Proof | null>(null)
  const [tests, setTests] = useState<TestRun | null>(null)
  const [mutation, setMutation] = useState<MutationRun | null>(null)
  const [runs, setRuns] = useState<GenerateRunSummary[]>([])
  const [sel, setSel] = useState<NavSelection | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [consoleOpen, setConsoleOpen] = useState(true)
  const [showNav, setShowNav] = useState(true)
  const [showProps, setShowProps] = useState(true)
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('cw-theme') as 'dark' | 'light') || 'dark')
  const shellRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => { applySavedPanes(shellRef.current) }, [])
  // A view opens at its top. On first load the Overview was measured opening at the
  // bottom of its scroll range (thesis clipped) while data arrived; pin it explicitly.
  const workRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    workRef.current?.querySelectorAll('.view-scroll').forEach((el) => { el.scrollTop = 0 })
  }, [view, updatedAt])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('cw-theme', theme)
  }, [theme])

  const loadRuns = useCallback(() => api.generateRuns().then(setRuns).catch(() => setRuns([])), [])

  const loadAll = useCallback(async () => {
    setBusy('load')
    log.cmd('reload --all')
    await Promise.allSettled([
      api.health().then((h) => {
        setHealth(h)
        const miss = Object.entries(h.tools).filter(([, ok]) => !ok).map(([k]) => k)
        if (miss.length) log.warn('TOOLS', `missing: ${miss.join(', ')} — the dependent stages will skip`)
        else log.info('TOOLS', 'verilator, iverilog, yosys, sby, z3 available')
        if (!h.stl_built) log.warn('STL', 'third_party/stl not built: STL-backed tests skip')
        if (h.hermes.model_available) log.info('HERMES', `${h.hermes.base_url} serving ${h.hermes.model} → ${h.hermes_model}`)
        else log.warn('HERMES', `not ready: ${h.hermes.error ?? 'model not listed'}`)
      }).catch(() => { setHealth(null); log.error('CONN', 'api-server unreachable on /api/health') }),
      api.design().then((d) => setModules(d.modules)).catch(() => {}),
      api.requirements().then((r) => {
        setReqs(r)
        log.info('REQS', `${r.items.length} requirements, ${r.items.filter((i) => i.design_choice).length} marked DESIGN`)
      }).catch(() => {}),
      api.gateLatest().then((g) => {
        setGate(g)
        const f = g.verdict === 'PROVEN' ? log.info : log.warn
        f('GATE', `latest archived: ${g.verdict} (${g.file})`)
      }).catch(() => log.note('GATE', 'no gate run archived yet')),
      api.mutationLatest().then((m) => {
        setMutation(m)
        log.info('MUTATE', `latest campaign: ${m.caught}/${m.total - m.equivalent} caught, ${m.survived} survived`)
      }).catch(() => {}),
      loadRuns(),
      api.tests().then((r) => {
        setTests(r)
        ;(r.returncode === 0 ? log.info : log.error)('PYTEST', `L0 oracle tests: ${r.summary}`)
      }).catch(() => {}),
    ])
    setUpdatedAt(new Date())
    setBusy(null)
  }, [loadRuns])

  useEffect(() => { void loadAll() }, [loadAll])

  async function runGate() {
    setBusy('gate'); setView('signoff')
    const r = await traced(
      { stage: 'signoff', tool: 'verilator+yosys+iverilog+sby', design: 'g711', cmd: 'python3 pipeline/gate.py --no-stop' },
      () => api.gate(),
      (v) => ({
        status: v.verdict === 'PROVEN' ? 'pass' : v.verdict === 'REJECTED' ? 'fail' : 'skip',
        extra: v.verdict,
        lines: () => {
          for (const [s, st] of Object.entries(v.stages)) {
            const f = st === 'pass' ? log.info : st === 'skip' ? log.warn : log.error
            f('STAGE', `${s}: ${st} — ${v.details[s] ?? ''}`)
          }
        },
      }))
    if (r) setGate(r)
    setBusy(null)
  }

  async function runTests() {
    setBusy('tests'); setView('spec')
    const r = await traced(
      { stage: 'L0', tool: 'pytest', design: 'oracles', cmd: '.venv/bin/python -m pytest tests -q' },
      () => api.tests(),
      (v) => ({
        status: v.returncode === 0 ? 'pass' : 'fail', extra: v.summary,
        lines: () => v.cases.filter((c) => c.status !== 'pass').forEach((c) =>
          (c.status === 'skip' ? log.warn : log.error)('PYTEST', `${c.name}: ${c.status} ${c.message}`)),
      }))
    if (r) setTests(r)
    setBusy(null)
  }

  async function runMutation() {
    setBusy('mutation'); setView('mutation')
    const t0 = performance.now()
    log.cmd('python3 pipeline/mutate.py -j 8')
    log.banner('mutation', 'gate × mutants', 'g711')
    try {
      const job = await api.mutationRun(8)
      const done = await waitJob<MutationRun>(job.id, undefined, 5000)
      if (done.status !== 'done' || !done.result) throw new Error(done.error ?? 'job failed')
      setMutation(done.result)
      for (const s of done.result.survivors) log.error('MUTATE', `survived: ${s.module} ${s.id} — ${s.after}`)
      log.verdict('mutation', done.result.survived ? 'fail' : 'pass', performance.now() - t0,
        `${done.result.caught}/${done.result.total - done.result.equivalent} caught`)
    } catch (e) {
      log.error('MUTATE', String(e)); log.verdict('mutation', 'fail', performance.now() - t0)
    }
    setBusy(null)
  }

  function exportTranscript() {
    const text = document.querySelector('.console-body')?.textContent ?? ''
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    a.download = `codec-agent-transcript-${new Date().toISOString().slice(0, 19).replace(/:/g, '')}.log`
    a.click(); URL.revokeObjectURL(a.href)
  }

  function openView(v: string, arg?: string) {
    if (v === 'rtl' && arg) setRtlModule(arg)
    setView(v as View)
  }

  const hermesReady = !!health?.hermes.model_available

  return (
    <div className="workbench" ref={shellRef}>
      <div className="menubar">
        <div className="brand">
          <span className="brand-mark">G711</span>
          <span className="brand-name">codec-agent Workbench</span>
          <span className="brand-design">g711</span>
          <span className="brand-thesis">{t('spec → RTL · the gate decides, not the model')}</span>
        </div>
        <Menu label="File">
          <MenuItem onClick={loadAll} accel="⌘R">{t('Reload all')}</MenuItem>
          <MenuItem onClick={exportTranscript}>{t('Export transcript…')}</MenuItem>
        </Menu>
        <Menu label="View">
          <MenuItem onClick={() => setShowNav((v) => !v)}>{showNav ? '✓ ' : ''}{t('Design navigator')}</MenuItem>
          <MenuItem onClick={() => setShowProps((v) => !v)}>{showProps ? '✓ ' : ''}{t('Properties')}</MenuItem>
          <MenuItem onClick={() => setConsoleOpen((v) => !v)}>{consoleOpen ? '✓ ' : ''}{t('Transcript')}</MenuItem>
          <MenuItem onClick={() => { resetPanes(shellRef.current); setShowNav(true); setShowProps(true); setConsoleOpen(true) }}>{t('Reset layout')}</MenuItem>
          <MenuItem onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? t('Light theme') : t('Dark theme')}</MenuItem>
          <MenuItem onClick={() => setLang(lang === 'ko' ? 'en' : 'ko')} accel="EN / 한">{lang === 'ko' ? 'English' : '한국어'}</MenuItem>
        </Menu>
        <Menu label="Run">
          <MenuItem onClick={runGate} disabled={!!busy}>{t('Run gate')}</MenuItem>
          <MenuItem onClick={runTests} disabled={!!busy}>{t('Run L0 tests')}</MenuItem>
          <MenuItem onClick={() => setView('generate')}>{t('Generate with Hermes…')}</MenuItem>
          <MenuItem onClick={runMutation} disabled={!!busy}>{t('Mutation campaign…')}</MenuItem>
        </Menu>
        <Menu label="Help">
          <MenuItem onClick={() => setView('overview')}>{t('What is this?')}</MenuItem>
          <MenuItem onClick={() => setView('docs')}>Docs</MenuItem>
          <MenuItem onClick={() => { log.info('ABOUT', 'codec-agent — G.711 spec→RTL with a tool-driven sign-off gate; UI adapted from hls-rtl'); setConsoleOpen(true) }}>{t('About')}</MenuItem>
        </Menu>
        <div className="menubar-right">
          <div className="lang-toggle" role="group" aria-label="language">
            {(['en', 'ko'] as Lang[]).map((l) => (
              <button key={l} className={`lang-btn ${lang === l ? 'lang-on' : ''}`} onClick={() => setLang(l)} aria-pressed={lang === l}>
                {l === 'en' ? 'EN' : '한'}
              </button>
            ))}
          </div>
          <span className={`conn ${health ? 'conn-ok' : 'conn-bad'}`}>{health ? t('connected') : t('offline')}</span>
        </div>
      </div>

      <div className="toolbar">
        <button className="tool" onClick={loadAll} disabled={!!busy}><span className="tool-ico">↻</span>{t('Reload')}</button>
        <span className="tool-sep" />
        <button className="tool" onClick={runTests} disabled={!!busy}><span className="tool-ico">✓</span>{t('Run L0 tests')}</button>
        <button className="tool" onClick={() => setView('generate')}><span className="tool-ico">✦</span>Generate</button>
        <button className="tool" onClick={() => setView('waveform')}><span className="tool-ico">◠</span>Waveform</button>
        <span className="tool-sep" />
        <button className="tool tool-primary" onClick={runGate} disabled={!!busy}>
          <span className="tool-ico">▶</span>{busy === 'gate' ? t('Running gate…') : t('Run gate')}
        </button>
        <div className="toolbar-right">
          {health && Object.entries(health.tools).map(([k, ok]) => (
            <span key={k} className={`pill ${ok ? 'pill-ok' : 'pill-warn'}`}>{k}</span>
          ))}
          <span className={`pill ${health?.stl_built ? 'pill-ok' : 'pill-warn'}`}>STL</span>
          <span className={`pill ${hermesReady ? 'pill-ok' : 'pill-warn'}`} title={health?.hermes.base_url}>
            hermes {hermesReady ? health?.hermes_model ?? 'ready' : 'not ready'}
          </span>
        </div>
      </div>

      <div className="workspace">
        {showNav && (
          <>
            <Navigator modules={modules} reqs={reqs} gate={gate} runs={runs} selected={sel} onSelect={setSel} />
            <Splitter axis="x" varName="--nav-w" min={150} max={520} label="Navigator width" />
          </>
        )}
        <main className="work-area">
          <div className="view-tabs">
            {VIEWS.map((v) => (
              <button key={v.id} className={`view-tab ${view === v.id ? 'view-tab-active' : ''}`} onClick={() => setView(v.id)}>{v.label}</button>
            ))}
          </div>
          <div className="view-body" ref={workRef}>
            {view === 'overview' && <Overview health={health} gate={gate} tests={tests} mutation={mutation} runs={runs} onGo={openView} />}
            {view === 'signoff' && <SignoffView proof={gate} busy={busy === 'gate'} onRun={runGate} onWave={(test) => showWave({ test })} />}
            {view === 'spec' && <SpecView reqs={reqs} tests={tests} busy={busy === 'tests'} onRunTests={runTests}
              onPick={(id) => { const r = reqs?.items.find((i) => i.id === id); if (r) setSel({ kind: 'req', id, data: r }) }} />}
            {view === 'mutation' && <MutationView run={mutation} busy={busy === 'mutation'} onRun={runMutation} />}
            {view === 'generate' && <GeneratePane health={health} onRunsChanged={() => void loadRuns()} onWave={showWave} />}
            {view === 'waveform' && <WaveformPane request={waveReq} />}
            {view === 'improve' && <ImprovePane />}
            {view === 'rtl' && (
              <>
                <div className="pane-toolbar">
                  {modules.map((m) => (
                    <button key={m.name} className={`tb-btn ${rtlModule === m.name ? 'tb-on' : ''}`} onClick={() => setRtlModule(m.name)}>{m.name}</button>
                  ))}
                </div>
                <RtlView module={rtlModule} />
              </>
            )}
            {view === 'docs' && <DocsView />}
          </div>
        </main>
        {showProps && (
          <>
            <Splitter axis="x" varName="--props-w" min={170} max={560} invert label="Properties width" />
            <Properties sel={sel} gate={gate} onOpen={openView} />
          </>
        )}
      </div>

      {consoleOpen && <Splitter axis="y" varName="--console-h" min={80} max={700} invert label="Transcript height" />}
      <Console open={consoleOpen} onToggle={() => setConsoleOpen((v) => !v)} />

      <div className="statusbar">
        <span className="sb-item">{t('design')}: <b>g711 ×{modules.length}</b></span>
        <span className="sb-item">{t('gate')}: <b className={gate?.verdict === 'PROVEN' ? 'sb-ok' : 'sb-fail'}>{gate?.verdict ?? '—'}</b></span>
        <span className="sb-item">mutants: <b>{mutation ? `${mutation.caught}/${mutation.total - mutation.equivalent}` : '—'}</b></span>
        <span className="sb-item">gen runs: <b>{runs.length}</b></span>
        <span className="sb-item">{t('model')}: <b>{health?.hermes_model ?? '—'}</b></span>
        <span className="sb-spacer" />
        {busy && <span className="sb-item sb-busy"><i className="spinner" />{busy}…</span>}
        <span className="sb-item muted">{updatedAt ? `${t('updated')} ${updatedAt.toLocaleTimeString()}` : t('loading…')}</span>
      </div>
    </div>
  )
}
