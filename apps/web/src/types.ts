import type {
  ActionPlan,
  Admission,
  EvidenceManifest,
  Metrics,
  Mode,
  NetworkScene,
  RunRecord,
  Series,
  SimulationResult,
  TaskSpec,
  VerificationReport,
} from '@waterguard/contracts'

export type SourceId = 'pair' | 'bench' | 'fixture'

/** 8001/8002 的 /api/benchmarks/current；不在公共契约里，按同学 1 的 index.json 实际字段声明。 */
export interface BenchCase {
  network_id: string
  case: string
  engine?: string
  simulation_id: string
  engine_status?: string
  validity?: string
  metrics: Metrics
  admission: Admission
  leakage_volume_m3?: number | null
}

export interface BenchIndex {
  schema_version: string
  mode: string
  run_id: string
  created_at?: string
  cases: BenchCase[]
  conclusion?: string
  attribution?: string
}

/** /api/simulations/{id}/physics，即 physical-checks.json。 */
export interface PhysicalChecks {
  kind?: string
  passed?: boolean
  mass_balance_max_abs_m3s?: number
  mass_balance_tolerance_m3s?: number
  interpretation?: string
  [k: string]: unknown
}

export interface CandidateView {
  key: string
  label: string
  simulationId: string
  /** 这个候选自己的冻结任务；场景库里正常工况与故障工况的任务不同（故障不同）。 */
  task: TaskSpec | null
  plan: ActionPlan | null
  verification: VerificationReport | null
  series: Series | null
  result: SimulationResult | null
  evidence: EvidenceManifest | null
  physics: PhysicalChecks | null
  engine: string | null
  caseName: string | null
  leakageVolume: number | null
  /** 这组数据实际读取的接口，用于“数字从哪来”。 */
  endpoints: string[]
}

export interface Workspace {
  source: SourceId
  mode: Mode | 'unknown'
  scene: NetworkScene
  task: TaskSpec
  candidates: CandidateView[]
  run: RunRecord | null
  runEvidence: EvidenceManifest | null
  index: BenchIndex | null
  apiBase: string
}

export interface BenchSelection {
  network: string
  engine: string
  caseName: string
}

export type KpiKey = 'shortage' | 'ratio' | 'minp' | 'actions' | 'violations'

export type Selection =
  | { type: 'node'; id: string }
  | { type: 'link'; id: string }
  | { type: 'kpi'; key: KpiKey }
  | null

export type Layer = 'pressure' | 'supply' | 'flow'
