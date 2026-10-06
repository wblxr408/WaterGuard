import type { TaskSpec } from '@waterguard/contracts';

export interface ThresholdPolicy {
  mode: 'adaptive' | 'manual';
  min_pressure_m: number;
  min_supply_ratio: number;
  baseline_min_pressure_m?: number;
  baseline_min_supply_ratio?: number;
  retention?: number;
  reference_simulation_id?: string;
}
export interface ThresholdRecommendation extends ThresholdPolicy { available: boolean; message?: string }
export function thresholdPolicy(task: TaskSpec): ThresholdPolicy | null {
  const prefix = 'WaterGuard thresholds v1: ';
  if (!task.hard_constraints.source.startsWith(prefix)) return null;
  try { return JSON.parse(task.hard_constraints.source.slice(prefix.length)) as ThresholdPolicy; }
  catch { return null; }
}
export function thresholdLabel(task: TaskSpec) {
  return thresholdPolicy(task)?.mode === 'adaptive' ? '自适应 · 无故障基线' : thresholdPolicy(task)?.mode === 'manual' ? '手动设置' : '预设阈值';
}
