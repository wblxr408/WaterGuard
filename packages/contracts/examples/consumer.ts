import type { TaskSpec, SimulationResult, VerificationReport, RunEvent } from '@waterguard/contracts';

// Type-only import: no Node or Python dependency is bundled into React.
export function candidateSummary(task: TaskSpec, result: SimulationResult, report: VerificationReport) {
  return {
    network: task.network_ref.network_id,
    mode: result.mode,
    pressure: report.metrics.min_pressure_m,
    layers: report.admission,
  };
}
export function eventKey(event: RunEvent): string {
  return `${event.run_id}:${event.sequence}`;
}
