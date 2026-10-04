import type {
  ActionPlan,
  EvidenceManifest,
  Mode,
  NetworkScene,
  RunRecord,
  Series,
  SimulationResult,
  TaskSpec,
  VerificationReport,
} from '@waterguard/contracts'
import { CASE_LABEL } from './format'
import type { BenchCase, BenchIndex, BenchSelection, CandidateView, PhysicalChecks, SourceId, Workspace } from './types'

export const SOURCES: Record<SourceId, { base: string; label: string; server: string }> = {
  pair: { base: '/ab', label: '真实对照 · Net3 A/B', server: '真实 A/B 对照接口（8002 端口）' },
  bench: { base: '/bm', label: '真实场景库 · 22 次计算', server: '真实场景库接口（8001 端口）' },
  fixture: { base: '/fx', label: '开发样例 · Fixture', server: 'Fixture 模拟接口（8000 端口）' },
}

export class ApiError extends Error {
  readonly url: string
  readonly status: number
  constructor(url: string, status: number, detail: string) {
    super(`${status || '无法连接'} · ${url}${detail ? ` · ${detail.slice(0, 160)}` : ''}`)
    this.url = url
    this.status = status
  }
}

interface Fetched<T> {
  data: T
  mode: string | null
}

async function get<T>(url: string): Promise<Fetched<T>> {
  let response: Response
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } })
  } catch {
    throw new ApiError(url, 0, '浏览器没能连上开发服务器')
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new ApiError(url, response.status, text)
  }
  return { data: (await response.json()) as T, mode: response.headers.get('X-WaterGuard-Mode') }
}

async function optional<T>(url: string): Promise<T | null> {
  try {
    return (await get<T>(url)).data
  } catch {
    return null
  }
}

function asMode(m: string | null | undefined): Mode | 'unknown' {
  return m === 'live' || m === 'replay' || m === 'fixture' || m === 'benchmark' ? m : 'unknown'
}

export function candidateLabel(id: string, engine?: string | null): string {
  const letter = /candidate-([a-z])$/i.exec(id)
  if (letter) return `方案 ${letter[1].toUpperCase()}`
  const base = CASE_LABEL[id] ?? id
  return engine ? `${base} · ${engine.toUpperCase()}` : base
}

async function benchCandidate(base: string, c: BenchCase): Promise<CandidateView> {
  const sid = c.simulation_id
  const urls = {
    result: `${base}/api/simulations/${sid}`,
    verification: `${base}/api/simulations/${sid}/verification`,
    task: `${base}/api/simulations/${sid}/task`,
    plan: `${base}/api/simulations/${sid}/plan`,
    evidence: `${base}/api/simulations/${sid}/evidence`,
    physics: `${base}/api/simulations/${sid}/physics`,
  }
  const [result, verification, task, plan, evidence, physics] = await Promise.all([
    get<SimulationResult>(urls.result),
    get<VerificationReport>(urls.verification),
    get<TaskSpec>(urls.task),
    get<ActionPlan>(urls.plan),
    optional<EvidenceManifest>(urls.evidence),
    optional<PhysicalChecks>(urls.physics),
  ])
  return {
    key: sid,
    label: candidateLabel(c.case),
    simulationId: sid,
    task: task.data,
    plan: plan.data,
    verification: verification.data,
    series: result.data.series,
    result: result.data,
    evidence,
    physics,
    engine: c.engine ?? null,
    caseName: c.case,
    leakageVolume: c.leakage_volume_m3 ?? null,
    endpoints: Object.values(urls),
  }
}

export async function loadPair(): Promise<Workspace> {
  const base = SOURCES.pair.base
  const index = await get<BenchIndex>(`${base}/api/benchmarks/current`)
  const cases = index.data.cases
  if (!cases.length) throw new ApiError(`${base}/api/benchmarks/current`, 200, '索引里没有工况')
  const first = cases[0]
  const [scene, candidates] = await Promise.all([
    get<NetworkScene>(`${base}/api/networks/${first.network_id}/scene`),
    Promise.all(cases.map((c) => benchCandidate(base, c))),
  ])
  return {
    source: 'pair',
    mode: asMode(index.mode ?? index.data.mode),
    scene: scene.data,
    task: candidates[0].task!,
    candidates,
    run: null,
    runEvidence: null,
    index: index.data,
    apiBase: base,
  }
}

export async function loadBench(sel: BenchSelection): Promise<Workspace> {
  const base = SOURCES.bench.base
  const index = await get<BenchIndex>(`${base}/api/benchmarks/current`)
  const cases = index.data.cases
  const pick = (network: string, engine: string, name: string) =>
    cases.find((c) => c.network_id === network && (c.engine ?? '') === engine && c.case === name)
  const selected = pick(sel.network, sel.engine, sel.caseName) ?? cases[0]
  const reference =
    selected.case === 'normal' ? undefined : pick(selected.network_id, selected.engine ?? '', 'normal')
  const chosen = reference ? [reference, selected] : [selected]
  const [scene, candidates] = await Promise.all([
    get<NetworkScene>(`${base}/api/networks/${selected.network_id}/scene`),
    Promise.all(chosen.map((c) => benchCandidate(base, c))),
  ])
  if (reference) candidates[0].label = '正常工况（对照）'
  return {
    source: 'bench',
    mode: asMode(index.mode ?? index.data.mode),
    scene: scene.data,
    task: candidates[candidates.length - 1].task!,
    candidates,
    run: null,
    runEvidence: null,
    index: index.data,
    apiBase: base,
  }
}

interface FixtureCandidate {
  plan: ActionPlan
  verification: VerificationReport
}

export async function loadFixture(): Promise<Workspace> {
  const base = SOURCES.fixture.base
  const [task, scene, run, list, evidence] = await Promise.all([
    get<TaskSpec>(`${base}/api/tasks/task-day1-closure`),
    get<NetworkScene>(`${base}/api/networks/wg-fixture-loop-v1/scene`),
    get<RunRecord>(`${base}/api/runs/run-day1-fixture`),
    get<FixtureCandidate[]>(`${base}/api/runs/run-day1-fixture/candidates`),
    optional<EvidenceManifest>(`${base}/api/runs/run-day1-fixture/evidence`),
  ])
  const candidates = await Promise.all(
    list.data.map(async ({ plan, verification }): Promise<CandidateView> => {
      const sid = verification.simulation_id
      const seriesUrl = `${base}/api/simulations/${sid}/series`
      const series = await get<Series>(seriesUrl)
      return {
        key: plan.candidate_id,
        label: candidateLabel(plan.candidate_id),
        simulationId: sid,
        task: task.data,
        plan,
        verification,
        series: series.data,
        result: null,
        evidence: null,
        physics: null,
        engine: null,
        caseName: null,
        leakageVolume: null,
        endpoints: [`${base}/api/runs/run-day1-fixture/candidates`, seriesUrl],
      }
    }),
  )
  return {
    source: 'fixture',
    mode: asMode(task.mode),
    scene: scene.data,
    task: task.data,
    candidates,
    run: run.data,
    runEvidence: evidence,
    index: null,
    apiBase: base,
  }
}

export function load(source: SourceId, sel: BenchSelection): Promise<Workspace> {
  if (source === 'pair') return loadPair()
  if (source === 'bench') return loadBench(sel)
  return loadFixture()
}
