/** Shapes returned by api/server.py. Kept close to the pipeline's own JSON. */

export type StageStatus = 'pass' | 'fail' | 'skip'
export type Verdict = 'PROVEN' | 'REJECTED' | 'INCONCLUSIVE'

export interface HermesHealth {
  base_url: string
  model: string
  key_configured: boolean
  health?: { status: string; platform: string; version: string }
  models?: string[]
  model_available?: boolean
  error?: string
}

export interface Health {
  ok: boolean
  tools: Record<string, boolean>
  stl_built: boolean
  spec_pdf: boolean
  hermes: HermesHealth
  hermes_model: string | null
}

export interface ModuleInfo {
  name: string
  role: string
  lines: number
  ports: string
  reqs: string[]
}

export interface Requirement {
  id: string
  section: string
  text: string
  source: string | null
  design_choice: boolean
}

export interface Requirements {
  items: Requirement[]
  exclude: string[]
}

export interface Proof {
  file?: string
  name: string
  verdict: Verdict
  rtl_dir: string
  stages: Record<string, StageStatus>
  details: Record<string, string>
  tests: Record<string, string>
  first_errors: Record<string, string>
  evidence_sha256: Record<string, string>
  rtl_sha256: Record<string, string>
  elapsed_s: number
  proves: string
  does_not_prove: string
  failure_evidence: string
}

export interface TestCase {
  file: string
  name: string
  status: StageStatus
  message: string
}

export interface TestRun {
  returncode: number
  elapsed_s: number
  summary: string
  cases: TestCase[]
}

export interface MutantResult {
  id: string
  module: string
  operator: string
  description: string
  line: number
  before: string
  after: string
  status: 'caught' | 'survived' | 'equivalent'
  caught_by: string[]
  stages: Record<string, StageStatus>
}

export interface MutationRun {
  file?: string
  total: number
  equivalent: number
  caught: number
  survived: number
  score_pct: number | null
  caught_by_stage: Record<string, number>
  caught_only_by_stage: Record<string, number>
  by_operator: Record<string, { caught: number; survived: number; equivalent: number }>
  elapsed_s: number
  survivors: MutantResult[]
  results: MutantResult[]
}

export interface Attempt {
  iteration: number
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }
  llm_elapsed_s?: number
  candidate?: string
  verdict?: Verdict
  stages?: Record<string, StageStatus>
  details?: Record<string, string>
  equivalent_to_baseline?: boolean
  gate_elapsed_s?: number
  evidence?: string
  error?: string
  reply_head?: string
}

export interface GenerateRun {
  run_id: string
  module: string
  outcome: 'PROVEN' | 'EXHAUSTED' | 'ERRORED'
  iterations: number
  max_iters: number
  model: string
  server: string
  proven_candidate: string | null
  equivalent_to_baseline: boolean | null
  attempts: Attempt[]
  proves: string
  note: string
}

export interface GenerateRunSummary {
  file: string
  kind: 'rtl' | 'testbench'
  kill_pct: number | null
  /** Verdict of a later, stricter gate on an archived PROVEN candidate. */
  regate?: string | null
  run_id: string
  module: string
  outcome: GenerateRun['outcome'] | 'ACCEPTED' | 'WEAK'
  iterations: number
  equivalent_to_baseline: boolean | null
  model: string
}

export interface Job<T = unknown> {
  id: string
  kind: string
  params: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  started: number
  finished: number | null
  result: T | null
  error: string | null
}

export interface DocFile {
  name: string
  path: string
  markdown: string
}

export interface TbKill {
  mutants: number
  considered: number
  killed: number
  kill_pct: number | null
  survivors: { id: string; operator: string; line: number; after: string; status: string }[]
}

export interface TbAttempt {
  iteration: number
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }
  llm_elapsed_s?: number
  testbench?: string
  check_elapsed_s?: number
  verdict?: 'ACCEPTED' | 'WEAK' | 'UNSOUND'
  sound?: boolean
  soundness?: { compiled: boolean; timeout?: boolean; result: string | null; checks: number | null }
  kill?: TbKill
  evidence?: string
  error?: string
  reply_head?: string
}

export interface TbRun {
  run_id: string
  module: string
  kind: 'testbench'
  outcome: 'ACCEPTED' | 'WEAK' | 'EXHAUSTED' | 'ERRORED'
  iterations: number
  max_iters: number
  min_kill_pct: number
  model: string
  testbench: string | null
  kill_pct: number | null
  attempts: TbAttempt[]
  note: string
}

// ---------- Waveform ----------
export interface WaveSignal {
  name: string
  width: number
  kind: string
  scope: string
  transitions: [number, string][]
}

export interface Waveform {
  available: boolean
  name?: string
  meta?: WaveCapture
  parameters?: { name: string; width: number; value: string }[]
  error?: string
  scope?: string
  scopes?: { name: string; signals: number }[]
  timescale_seconds?: number
  time_unit?: string
  start?: number
  end?: number
  truncated?: boolean
  transition_count?: number
  signals?: WaveSignal[]
}

export interface WaveCapture {
  available?: boolean
  name: string
  result: string | null
  first_error: string | null
  window: { start_cycle: number; cycles: number; reason: string }
  vcd: string
  elapsed_s?: number
  size_bytes?: number
  error?: string
}

export interface GateTestInfo { name: string; mode: number; n: number; bp: number; stem: string }
