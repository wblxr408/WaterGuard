# WaterGuard 工业工作台视觉重构

日期：2026-10-08。入口：`apps/web-gpt`。用户要求先研究同类成熟产品，再解决两侧留空、配色和布局带来的 AI 模板感。

## 产品与设计依据

| 官方来源 | 可以确认的依据 | 本次设计取舍 |
| --- | --- | --- |
| [Bentley OpenFlows WaterSight](https://www.bentley.com/en/products/openflows-watersight/) | 将管网监测、故障定位、泵站分析和应急仿真组织在统一的数字孪生环境 | 管网是主工作面，结果与实体详情相邻，时间轴贴近空间视图 |
| [WaterSight 使用文档](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0043069) | 左侧菜单进入不同功能模块，页面提供上下文帮助 | 桌面用固定侧栏稳定导航，窄笔记本收成图标栏，移动端改为可横向滚动的导航 |
| [Xylem Vue](https://www.xylem.com/en-sg/brand/vue/our-solutions/xylem--vue/) | 将多来源数据整合为单一模型，通过模块化应用展示指标、告警和行动 | 减少重复标题与装饰，让当前工况、约束、状态和下一操作可同时辨认。该链接的搜索索引可读，直接抓取返回 404，未据此声称已检查其当前登录界面 |
| [Schneider EcoStruxure Water Advisor](https://www.se.com/us/en/product-range/196440546-ecostruxure-water-advisor/?parent-subcategory-id=170323886) | 水务软件覆盖设计、规划、运行、维护和优化 | 建模、运行、比较、证据沿用同一套导航、表格和控件规则 |
| [IBM Carbon 2x Grid](https://www.carbondesignsystem.com/building-blocks/foundations/2x-grid/code) 与 [全宽网格说明](https://v10.carbondesignsystem.com/guidelines/2x-grid/implementation/) | 用网格组织复杂布局，支持全宽布局与 UI Shell | 删除居中工作区的最大宽度限制，桌面主内容外侧保持 24–28px 间距 |
| [IBM Carbon 色彩角色](https://v10.carbondesignsystem.com/guidelines/color/usage/) | 为背景、边框、正文、交互与危险状态定义不同的颜色角色 | 石墨色页头、灰白工作面、低饱和青绿操作色；成功、低值、违反和待验证保持各自的语义 |

“高级感”在此被解释为专业、稳定、清晰与克制，是本次设计判断。没有将厂商官网宣传页等同于登录后产品，也没有声称采用官方 Carbon 组件。

## 已实现

- 56px 全局栏；桌面 208px 导航、大屏 224px；1250px 以下桌面收成 72px 图标栏；760px 以下横向导航。
- 工作区随窗口铺开，三栏尺寸由指标阅读、管网观察和详情阅读的实际需要决定。
- 工况与验收使用可展开区域，收起仍显示工况名称及压力、供需比阈值，展开提供工况选择和阈值编辑。
- 无原生运行时去掉空阶段条，保留方案提出者；有真实运行时继续展示实际阶段和记录。
- 业务页取消紫色、香槟色渐变与大型浮动阴影；表格、方案页、建模展台、Jev、工作流和弹窗统一边框与圆角。
- 保留团队 Blender 闸阀拆装序列与水面转场，首页改用石墨色背景，减少发光装饰；首页系统预览重新截取实际页面。
- 页面支持 `?page=overview|agent|compare|model|evidence` 直接访问；没有 page 参数时沿用首页入口；run 参数继续按原有流程解析。

## 验证

真实本地数据服务和无头 Microsoft Edge 下，六页在 1920×1080、1440×900、1024×768、390×844、320×844 共 30 个组合均无页面横向溢出。1440×900 的应急工作台完整呈现管网及时间轴。原始测量、桌面和手机截图在本机忽略目录 `.cache/ui-review/final/`。

交互复核覆盖候选切换、二维节点编号定位、时间轴、任务详情、新建任务弹窗、专注模式与证据搜索；构建和现有工作流测试用于确认样式与导航调整没有破坏工作流。本次未提交新任务或修改模型账号。构建发现事件类型表缺少已有契约中的 `clarification.requested`，已补到任务建模节点。
