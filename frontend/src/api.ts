import type {
  DocFile, GateTestInfo, GenerateRun, TbRun, WaveCapture, Waveform, GenerateRunSummary, Health, Job, ModuleInfo, MutationRun, Proof,
  Requirements, TestRun,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.detail ?? JSON.stringify(body)
    } catch {
      detail = await res.text()
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ''}`)
  }
  return (await res.json()) as T
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  health: () => request<Health>('/health'),
  design: () => request<{ modules: ModuleInfo[] }>('/design'),
  rtl: (module: string) => request<{ module: string; path: string; source: string }>(`/rtl/${module}`),
  requirements: () => request<Requirements>('/requirements'),

  /** The committed RTL, or a generated candidate (repo-relative .sv path). Runs every stage. */
  gate: (candidate?: string) => post<Proof>('/gate', { candidate: candidate ?? null }),
  gateLatest: () => request<Proof>('/gate/latest'),
  tests: () => post<TestRun>('/tests'),

  mutationLatest: () => request<MutationRun>('/mutation/latest'),
  mutationRun: (jobs = 8) => post<Job<MutationRun>>('/mutation/run', { jobs }),

  generate: (module: string, maxIters: number) =>
    post<Job<GenerateRun>>('/generate', { module, max_iters: maxIters }),
  generateTb: (module: string, maxIters: number, minKill = 90) =>
    post<Job<TbRun>>('/generate_tb', { module, max_iters: maxIters, min_kill: minKill }),
  generatePrompt: (module: string) => request<{ prompt: string; tb_prompt: string }>(`/generate/prompt/${module}`),
  generateRuns: () => request<GenerateRunSummary[]>('/generate/runs'),
  generateRun: (file: string) => request<GenerateRun | TbRun>(`/generate/runs/${encodeURIComponent(file)}`),
  candidate: (path: string) => request<{ path: string; source: string }>(`/candidate?path=${encodeURIComponent(path)}`),

  waveTests: () => request<GateTestInfo[]>('/waves/tests'),
  waveCaptures: () => request<WaveCapture[]>('/waves/captures'),
  waveCapture: (req: { test?: string; candidate?: string; testbench?: string; module?: string; cycles?: number }) =>
    post<WaveCapture>('/waves/capture', req),
  wave: (name: string, scope?: string) =>
    request<Waveform>(`/waves/${encodeURIComponent(name)}${scope ? `?scope=${encodeURIComponent(scope)}` : ''}`),

  job: <T>(id: string) => request<Job<T>>(`/jobs/${id}`),
  doc: (name: string) => request<DocFile>(`/doc/${name}`),
}

/** Poll a background job until it leaves `running`. */
export async function waitJob<T>(id: string, onTick?: (j: Job<T>) => void, everyMs = 2000): Promise<Job<T>> {
  for (;;) {
    const j = await api.job<T>(id)
    onTick?.(j)
    if (j.status !== 'running') return j
    await new Promise((r) => setTimeout(r, everyMs))
  }
}
