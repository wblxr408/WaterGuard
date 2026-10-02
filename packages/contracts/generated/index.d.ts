/* Generated from schema/waterguard.schema.json. Run npm run contracts:types. */

/**
 * Frozen development contracts. Structural validation must be followed by semantic and provenance checks.
 */
export type WaterGuardContract =
  | NetworkManifest
  | TaskSpec
  | ActionPlan
  | ActionCatalog
  | NetworkScene
  | SimulationResult
  | VerificationReport
  | RunRecord
  | RunEvent
  | EvidenceManifest;
export type Hash = string;
export type TaskSpec = {
  [k: string]: unknown;
} & {
  schema_version: "0.1.0" | "0.2.0";
  task_id: string;
  revision: number;
  network_ref: NetworkRef;
  fault: Fault | null;
  time_window: TimeWindow;
  /**
   * @minItems 1
   */
  objectives: ["minimize_shortage" | "minimize_action_cost", ...("minimize_shortage" | "minimize_action_cost")[]];
  hard_constraints: HardConstraints;
  /**
   * @minItems 0
   */
  soft_preferences: string[];
  action_catalog_ref: string;
  action_catalog_hash: Hash;
  action_limits: ActionLimits;
  perturbation_domain: PerturbationDomain;
  /**
   * @minItems 1
   */
  assumptions: [string, ...string[]];
  budget: Budget;
  constraint_hash: Hash;
};
export type NetworkScene = {
  [k: string]: unknown;
} & {
  schema_version: "0.1.0" | "0.2.0";
  network_id: string;
  mode: Mode;
  coordinate_system: string;
  provenance: "synthetic" | "derived";
  /**
   * @minItems 1
   */
  nodes: [SceneNode, ...SceneNode[]];
  /**
   * @minItems 1
   */
  links: [SceneLink, ...SceneLink[]];
  display_coordinate_note?: string;
};
export type Mode = "live" | "replay" | "fixture" | "benchmark";
export type RunStatus = "QUEUED" | "RUNNING" | "NEEDS_INPUT" | "COMPLETED" | "BUDGET_EXHAUSTED" | "ERROR" | "CANCELLED";
export type RunPhase =
  "INTAKE" | "MODELING" | "BASELINE" | "PLANNING" | "SIMULATING" | "VERIFYING" | "REVIEWING" | "PACKAGING";

export interface NetworkManifest {
  schema_version: "0.1.0" | "0.2.0";
  network_id: string;
  revision: number;
  sha256: Hash;
  source_url: string;
  source_kind: "synthetic" | "public" | "measured";
  license: string;
  units: Units;
  coordinate_system: string;
  /**
   * @minItems 0
   */
  overlays: Overlay[];
  original_file: string;
  description: string;
}
export interface Units {
  length: "m";
  pressure_head: "m";
  flow: "m3/s";
  time: "s";
}
export interface Overlay {
  ref: string;
  sha256: Hash;
  provenance: "assumed" | "synthetic" | "measured";
  description: string;
}
export interface NetworkRef {
  network_id: string;
  revision: number;
  sha256: Hash;
  overlay_hash: Hash;
}
export interface Fault {
  kind: "leak" | "rupture" | "closure" | "isolation";
  link_id: string;
  start_s: number;
  end_s: number;
  mapping: string;
}
export interface TimeWindow {
  start_s: number;
  end_s: number;
  step_s: number;
  warmup_end_s: number;
  evaluation_start_s: number;
}
export interface HardConstraints {
  min_pressure_m: number;
  min_supply_ratio: number;
  /**
   * @minItems 1
   */
  node_ids: [string, ...string[]];
  pressure_tolerance_m: number;
  ratio_tolerance: number;
  source: string;
}
export interface ActionLimits {
  max_actions: number;
  /**
   * @minItems 1
   */
  allowed_link_ids: [string, ...string[]];
  /**
   * @minItems 1
   */
  allowed_kinds: [
    "link_open" | "link_close" | "pump_on" | "pump_off",
    ...("link_open" | "link_close" | "pump_on" | "pump_off")[]
  ];
  earliest_s: number;
  latest_s: number;
  min_switch_interval_s: number;
}
export interface PerturbationDomain {
  /**
   * @minItems 0
   */
  required: string[];
  /**
   * @minItems 1
   */
  demand_multipliers: [number, ...number[]];
}
export interface Budget {
  max_simulations: number;
  max_parallel: number;
  max_wall_time_s: number;
  max_revisions: number;
}
export interface ActionPlan {
  schema_version: "0.1.0" | "0.2.0";
  candidate_id: string;
  task_ref: TaskRef;
  parent_candidate_id: string | null;
  /**
   * @minItems 0
   */
  actions: Action[];
  source: "fixture" | "agh" | "rule" | "enumeration" | "graph";
  reason: string;
  plan_hash: Hash;
}
export interface TaskRef {
  task_id: string;
  revision: number;
  network_hash: Hash;
  constraint_hash: Hash;
}
export interface Action {
  kind: "link_open" | "link_close" | "pump_on" | "pump_off";
  link_id: string;
  simulation_time_s: number;
}
export interface ActionCatalog {
  schema_version: "0.1.0" | "0.2.0";
  catalog_id: string;
  network_id: string;
  /**
   * @minItems 1
   */
  facilities: [Facility, ...Facility[]];
}
export interface Facility {
  link_id: string;
  kind: "pipe" | "pump" | "valve";
  /**
   * @minItems 1
   */
  allowed_actions: [
    "link_open" | "link_close" | "pump_on" | "pump_off",
    ...("link_open" | "link_close" | "pump_on" | "pump_off")[]
  ];
  initial_status: "open" | "closed";
  action_cost: number;
  mapping: string;
}
export interface SceneNode {
  id: string;
  kind: "junction" | "reservoir" | "tank";
  x_m: number;
  y_m: number;
  elevation_m: number;
  critical: boolean;
}
export interface SceneLink {
  id: string;
  from_node: string;
  to_node: string;
  kind: "pipe" | "pump" | "valve";
  initial_status: "open" | "closed";
}
export interface SimulationResult {
  schema_version: "0.1.0" | "0.2.0";
  mode: Mode;
  artifact: SimulationArtifact;
  series: Series | null;
  diagnostics: Diagnostics;
}
export interface SimulationArtifact {
  schema_version: "0.1.0" | "0.2.0";
  simulation_id: string;
  candidate_id: string;
  task_ref: TaskRef;
  perturbation_id: string | null;
  mode: Mode;
  engine_version: string;
  input_hash: Hash;
  status: "SUCCEEDED" | "FAILED" | "CANCELLED";
  series_ref: string | null;
  diagnostics_ref: string;
  time_axis: TimeWindow;
}
export interface Series {
  schema_version: "0.1.0" | "0.2.0";
  simulation_id: string;
  mode: Mode;
  units: Units;
  sampling: "interval_start_hold";
  /**
   * @minItems 1
   */
  frames: [SeriesFrame, ...SeriesFrame[]];
}
export interface SeriesFrame {
  time_s: number;
  duration_s: number;
  /**
   * @minItems 1
   */
  nodes: [NodeSample, ...NodeSample[]];
  /**
   * @minItems 1
   */
  links: [LinkSample, ...LinkSample[]];
}
export interface NodeSample {
  node_id: string;
  pressure_m: number;
  expected_demand_m3s: number;
  delivered_demand_m3s: number;
  leak_flow_m3s: number;
}
export interface LinkSample {
  link_id: string;
  flow_m3s: number;
  status: "open" | "closed";
}
export interface Diagnostics {
  schema_version: "0.1.0" | "0.2.0";
  simulation_id: string;
  mode: Mode;
  converged: boolean | null;
  /**
   * @minItems 0
   */
  errors: string[];
  /**
   * @minItems 0
   */
  warnings: string[];
  provenance: string;
}
export interface VerificationReport {
  schema_version: "0.1.0" | "0.2.0";
  verification_id: string;
  simulation_id: string;
  candidate_id: string;
  task_ref: TaskRef;
  mode: Mode;
  validity: "valid" | "invalid";
  /**
   * @minItems 0
   */
  invalid_reasons: string[];
  /**
   * @minItems 0
   */
  violations: Violation[];
  metrics: Metrics;
  coverage: Coverage;
  admission: Admission;
  /**
   * @minItems 0
   */
  recommendation_blockers: string[];
  verifier_version: string;
}
export interface Violation {
  code: "MIN_PRESSURE" | "MIN_SUPPLY_RATIO";
  node_id: string;
  start_s: number;
  end_s: number;
  observed: number;
  threshold: number;
  unit: "m" | "ratio";
}
export interface Metrics {
  shortage_m3: number;
  supply_ratio: number | null;
  min_pressure_m: number | null;
  critical_node_time_pass_ratio: number | null;
  action_cost: number;
}
export interface Coverage {
  start_s: number;
  end_s: number;
  /**
   * @minItems 1
   */
  assessed_node_ids: [string, ...string[]];
  /**
   * @minItems 0
   */
  required_stress_tests: string[];
  /**
   * @minItems 0
   */
  completed_stress_tests: string[];
}
export interface Admission {
  action_valid: "pass" | "fail" | "pending";
  simulation_valid: "pass" | "fail" | "pending";
  nominal_constraints_pass: "pass" | "fail" | "pending";
  required_stress_tests_pass: "pass" | "fail" | "pending";
  recommendation_eligible: "pass" | "fail" | "pending";
}
export interface RunRecord {
  schema_version: "0.1.0" | "0.2.0";
  run_id: string;
  task_ref: TaskRef;
  mode: Mode;
  status: RunStatus;
  phase: RunPhase;
  agh_session_ref: string | null;
  model_config_ref: string | null;
  budget_usage: {
    simulations: number;
    model_calls: number | null;
    wall_time_s: number;
  };
  conclusion: "FEASIBLE_FOUND" | "NO_FEASIBLE_FOUND" | "INCOMPLETE" | "INVALID";
  selected_candidate_id: string | null;
  /**
   * @minItems 0
   */
  missing_items: string[];
}
export interface RunEvent {
  schema_version: "0.1.0" | "0.2.0";
  event_id: string;
  sequence: number;
  run_id: string;
  task_revision: number;
  constraint_hash: Hash;
  mode: Mode;
  agh_session_ref: string | null;
  native_tool_call_ref: string | null;
  native_reference_missing_reason: string | null;
  correlation_id: string;
  origin: "agh" | "gateway" | "worker" | "verifier" | "user";
  type: "run.started" | "candidate.submitted" | "simulation.completed" | "verification.completed" | "run.completed";
  candidate_id: string | null;
  simulation_id: string | null;
  occurred_at: string;
  simulation_time_s: number | null;
  payload_ref: string;
}
export interface EvidenceManifest {
  schema_version: "0.1.0" | "0.2.0";
  bundle_id: string;
  run_id: string;
  mode: Mode;
  native_trace_ref: string | null;
  association_ref: string | null;
  /**
   * @minItems 1
   */
  artifacts: [EvidenceArtifact, ...EvidenceArtifact[]];
  hashes: {
    [k: string]: Hash;
  };
  /**
   * @minItems 0
   */
  redactions: string[];
  /**
   * @minItems 0
   */
  missing_items: string[];
  live_export_allowed: boolean;
}
export interface EvidenceArtifact {
  ref: string;
  sha256: Hash;
  kind: string;
}
