# Proposal

## Why

现有工作台能验证单个候选，却不能直观看到需求和抢修时间变化时的失效范围，也不能比较补救条件。增加可计算、可交互的决策实验，让每次仿真用于发现边界、选择补救或更新剩余计划。

## What Changes

- 基于现有冻结任务和候选开展独立的决策研究，不修改历史报告。
- 需求倍率×抢修完成时刻的离散边界地图，预算内优先探测已测状态不同的邻域；未知点保持未知。
- 加入粗/精水力计算及物理信息残差代理：粗解筛选、图物理正则化和 PDD 约束预测，用高精度独立验证更新代理；全部求解计入同一预算。
- 逐项重算提前抢修与增加许可动作的补救选项，输出最小已测有效补救和缺供变化。
- 抢修时间变化触发重评，保留事件时刻之前的动作，未来动作遵守新的响应延迟。
- React 工作台增加决策实验页面，展示地图、原始验证详情、补救选项和更新计划。

## Capabilities

### New Capabilities
- `decision-studies`: 有限预算的边界探索、补救比较及事件重评。

### Modified Capabilities
无。

## Impact

增加 services/api/decision_studies.py、services/hydraulics/surrogate.py 和 API 路由、前端决策实验组件；复用 PhysicalAdapter、verify、benchmark_plans、ranking_key、RunStore 原子 JSON 存储及已有 NumPy/SciPy/NetworkX，不引入新服务、数据库或大型 ML 依赖；此研究工具不增加现场设备控制权限。
