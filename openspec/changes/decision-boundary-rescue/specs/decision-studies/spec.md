# Spec Delta

## Purpose

让供水调度员以已有任务和候选开展独立、可追溯的决策实验，在有限仿真预算内理解需求与抢修变化的影响，比较实际计算的补救条件，并在抢修事件变化时更新后续动作，保留原始任务和历史证据。

## ADDED Requirements

### Requirement: Budgeted boundary exploration
系统 SHALL 在用户声明的需求倍率与抢修时刻网格内执行有限预算仿真；每个已测点返回独立验证的状态、指标及证据引用，未计算点保持 unknown，不插值为通过。

#### Scenario: Partial grid
- **WHEN** 网格含九个点且用户预算为五次
- **THEN** 最多计算五点，其余四点保持 unknown，并优先选择状态变化邻域。

### Requirement: Tested rescue comparison
系统 SHALL 按用户声明的补救成本逐项计算提前抢修和增加许可动作选项，比较相同条件下的基线；只从实际已测达标选项中选择成本最低者，并保留未完成项。

#### Scenario: Cheaper rescue fails
- **WHEN** 较便宜补救未达标而较贵补救通过同口径验证
- **THEN** 选择较贵的已测达标项，明确结论仅覆盖已测选项。

### Requirement: Physics-informed multi-fidelity screening
系统 SHALL 可选使用粗时间步长水力结果和具有图物理正则化及 PDD 约束的残差代理选择高精度复验点；粗解和精解共享总次数及时间预算。代理不确定度为经验排序指标，预测和粗解不能将地图点标为验证达标。

#### Scenario: Coarse pass is not a verified pass
- **WHEN** 某点只有粗仿真通过且尚未完成高精度复验
- **THEN** 地图该点仍为 unknown，显示单独的粗解或代理预测，并展示两种保真的实际次数。

#### Scenario: Online residual correction
- **WHEN** 已获得至少两个点的粗精配对压力结果
- **THEN** 使用配对残差更新代理，优先复验接近压力或供水边界且不确定性较高的点，保存复验前预测误差。

### Requirement: Event-based reassessment
系统 SHALL 在抢修完成时间变化后保留事件前的原计划动作，以声明的响应时间为下限生成后续动作，并对相同事件下的原计划与新候选重新计算比较。

#### Scenario: Executed actions preserved
- **WHEN** 事件发生前已经执行一次启泵，事件后修理延迟
- **THEN** 更新计划保留该启泵动作，新增动作不能早于事件加响应时间，原任务与报告不变。

### Requirement: Inspectable study lifecycle
系统 SHALL 提供研究创建、进度读取、取消、列表和结果读取；界面显示未知、正常层达标、基本供水达标、存在缺口和计算无效，并允许查看所选点的验证详情。

#### Scenario: Cancel during calculation
- **WHEN** 用户取消仍在计算的研究
- **THEN** 当前求解收到取消信号，已完成结果保留，后续点不继续派发。
