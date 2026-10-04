# AGH 基座与启动

WaterGuard 使用 [官方 agnes-harness](https://github.com/AgnesAI-Labs/agnes-harness) 作为模型与工具执行基座。源码位于 Git 子模块 `vendor/agnes-harness`，当前固定到 `fe90962a6b8561d8cae7e5d451905604fcf475cf`。业务扩展位于 `packages/waterguard-agh`，水力计算、任务冻结、验证和证据服务位于 `services/`。

## 本机启动

```powershell
cd D:\WaterGuard
npm start
```

另开终端运行 `npm run start:web`，打开唯一的 [WaterGuard 工作台](http://127.0.0.1:5173/)，在“智能体 → 模型账号”配置模型。AGH 启动器自动选择 Node >=24.10，在需要时启动 8010 Python API，并使用独立的 `.cache/agh-waterguard` 配置目录。[官方 AGH 工作台](http://127.0.0.1:4180/) 可用于检查会话与插件。关闭启动器按 Ctrl+C；仅停止它自己启动的 API，官方共享 daemon 按 AGH 生命周期管理。

任务冻结与运行入口为新版智能体页，[API 文档](http://127.0.0.1:8010/docs) 提供完整接口。单独启动 API 用 `npm run start:api`。API 根路径重定向到 5173。启动器支持 `npm start -- --port 4181 --api-port 8011`，自定义 API 端口时须同步 Vite 代理和插件地址，也接受 Windows npm 转发的两个端口数值。

官方 daemon 会绑定已选 Web origin。改变 AGH 端口前，退出 Web，在使用 Node 24 的终端停止本项目的 daemon 再启动新端口；仅重启 Web 会得到 origin 不匹配错误：

```powershell
$env:AGH_HOME = 'D:\WaterGuard\.cache\agh-waterguard'
node vendor/agnes-harness/packages/cli/dist/local/agnes.mjs daemon stop --profile local-dev --data-dir D:/WaterGuard/.cache/agh-waterguard/data
npm start -- --port 4181 --api-port 8011
```

## 首次构建

需要 Python 水力依赖、Node >=24.10、pnpm 10.34.5；Windows 原生构建还需 Visual Studio C++ Build Tools 和 Windows SDK。先安装 Python 依赖：

```powershell
cd D:\WaterGuard
git submodule update --init --recursive
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/api/requirements.txt -r services/hydraulics/requirements.txt
```

在 Node 24.10 或更新版本位于 PATH 的终端执行：

```powershell
cd D:\WaterGuard\vendor\agnes-harness
pnpm install --frozen-lockfile
# Windows 使用官方脚本准备 Node 头文件：
.\.github\scripts\prepare-windows-native.ps1 -CacheRoot "$env:LOCALAPPDATA/node-gyp/Cache"
pnpm --filter @agnes/system-node build:native
pnpm --filter @agnes/cli build:local
cd D:\WaterGuard
npm start
```

另开终端安装业务插件。下面使用官方 Node SDK 连接身份校验过的 daemon，检查实际包内容哈希后安装、更新、信任和启用插件；重复运行会检查已有版本。

```powershell
cd D:\WaterGuard
node --import ./vendor/agnes-harness/node_modules/tsx/dist/loader.mjs scripts/configure_agh.mjs
```

模型凭据在新版“智能体 → 模型账号”输入，支持赛事 Agnes、DeepSeek 与 GLM，具体地址见 [模型接入](模型接入.md)。也可在官方 AGH 设置页配置，或在本机通过 `AGNES_API_KEY` 环境变量交给配置脚本。只有官方验证成功后才保存账号，报告不包含密钥。当前没有有效存储的模型账号，因此模型驱动的 Live 尚未验证。

## 使用业务工具

在 WaterGuard 工作台确认任务、选择 Live 并启动。后端通过官方 SDK 创建 AGH 会话，冻结模型选择与任务版本，由 AGH 中的模型读取任务、生成候选、选择并提交方案、仿真、独立验证及读取证据。工具名称与执行边界见 [原生插件](../packages/waterguard-agh/README.md)。官方工作台也可手工创建以 `D:\WaterGuard` 为工作目录的会话调用工具。

当前已验证原生插件加载、分步工具调用及模型配置接口。SDK Live 执行路径已实现；真实模型会话、原生 trace 与运行证据关联仍需有效凭据完成验收。`mode=live` 在缺少账号时返回 `503 MODEL_NOT_CONFIGURED`，daemon 不可用时返回 `503 AGH_UNAVAILABLE`。缺失正式准入证据时，`live_export_allowed` 保持 false。

## 验证与更新官方源码

实际 HTTP 验收及插件工具验收：

```powershell
.\.venv\Scripts\python.exe scripts/check_backend_api.py
node --import ./vendor/agnes-harness/node_modules/tsx/dist/loader.mjs scripts/check_agh_tools.mjs
```

报告保存在 `artifacts/api-acceptance/report.json`、`artifacts/agh-integration/tools.json`。这些是本地验收产物，不是 Agnes 模型产生的记录。

需要更新基座时先检查上游差异，选择并固定新提交，再按首次构建步骤重建、重启本项目 daemon、重跑插件配置和验收。示例：

```powershell
git -C vendor/agnes-harness fetch origin main
git -C vendor/agnes-harness log --oneline HEAD..origin/main
# 审阅后将 <已审阅的提交> 替换成实际 SHA：
git -C vendor/agnes-harness checkout --detach <已审阅的提交>
```

根仓库的子模块引用记录基座版本；WaterGuard 插件修改在根仓库管理。
