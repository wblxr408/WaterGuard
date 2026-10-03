# 从仓库或应用目录运行均可。只关闭本脚本启动的进程，保留已经运行的服务。
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$wgAppRoot = Split-Path $PSScriptRoot -Parent
$wgRepoRoot = [IO.Path]::GetFullPath((Join-Path $wgAppRoot '../..'))
$wgPython = Join-Path $wgRepoRoot '.venv/Scripts/python.exe'
$wgStarted = [Collections.Generic.List[System.Diagnostics.Process]]::new()
if (-not (Test-Path -LiteralPath $wgPython)) {
    throw '请先按 README.md 创建仓库根目录 .venv 并安装 services/hydraulics/requirements.txt。'
}
if (-not (Test-Path -LiteralPath (Join-Path $wgAppRoot 'node_modules'))) {
    throw '请先在 apps/web-gpt 中执行 npm ci。'
}
New-Item -ItemType Directory -Force (Join-Path $wgAppRoot '.cache') | Out-Null
Push-Location $wgRepoRoot
try {
    & $wgPython -c "import zipfile,pathlib; pairs=[('feedback-pair.zip','feedback-pair'),('verified-results.zip','student1')]; [(zipfile.ZipFile('benchmarks/student1/'+src).extractall('artifacts/'+dst)) for src,dst in pairs if not pathlib.Path('artifacts/'+dst+'/index.json').exists()]"
    if ($LASTEXITCODE -ne 0) { throw '计算证据解压失败。' }
    $wgServices = @(
        @{ Port = 8000; Mode = 'fixture'; Path = 'fixture'; Args = @('packages/contracts/scripts/mock_server.py') },
        @{ Port = 8001; Mode = 'benchmark'; Path = 'benchmarks/current'; Args = @('-m','services.hydraulics.server','--output','artifacts/student1','--port','8001') },
        @{ Port = 8002; Mode = 'benchmark'; Path = 'benchmarks/current'; Args = @('-m','services.hydraulics.server','--output','artifacts/feedback-pair','--port','8002') }
    )
    foreach ($wgService in $wgServices) {
        $wgUrl = "http://127.0.0.1:$($wgService.Port)/api/$($wgService.Path)"
        $wgResponse = $null
        try { $wgResponse = Invoke-WebRequest $wgUrl -TimeoutSec 2 } catch { }
        if ($wgResponse) {
            if ($wgResponse.Headers['X-WaterGuard-Mode'] -ne $wgService.Mode) { throw "端口 $($wgService.Port) 被不匹配的服务占用。" }
            Write-Host "复用已有服务：$($wgService.Port) / $($wgService.Mode)"
            continue
        }
        $wgProcess = Start-Process -FilePath $wgPython -ArgumentList $wgService.Args -WorkingDirectory $wgRepoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $wgAppRoot ".cache/backend-$($wgService.Port).out.log") -RedirectStandardError (Join-Path $wgAppRoot ".cache/backend-$($wgService.Port).err.log")
        $wgStarted.Add($wgProcess)
        $wgReady = $false
        for ($wgAttempt = 0; $wgAttempt -lt 12; $wgAttempt++) {
            Start-Sleep -Milliseconds 500
            try {
                $wgResponse = Invoke-WebRequest $wgUrl -TimeoutSec 2
                if ($wgResponse.Headers['X-WaterGuard-Mode'] -eq $wgService.Mode) { $wgReady = $true; break }
            } catch { }
            if ($wgProcess.HasExited) { break }
        }
        if (-not $wgReady) { throw "后端 $($wgService.Port) 未启动；请查看 apps/web-gpt/.cache/backend-$($wgService.Port).err.log。" }
        Write-Host "后端已就绪：$($wgService.Port) / $($wgService.Mode)"
    }
    if ($CheckOnly) { Write-Host '三个数据源检查通过。'; return }
    Set-Location $wgAppRoot
    Write-Host 'WaterGuard 前端：http://127.0.0.1:5173/ （Ctrl+C 结束）'
    & npm.cmd run dev
    if ($LASTEXITCODE -ne 0) { throw 'Vite 启动失败，请检查 5173 端口占用。' }
} finally {
    foreach ($wgProcess in $wgStarted) {
        if (-not $wgProcess.HasExited) { Stop-Process -Id $wgProcess.Id -ErrorAction SilentlyContinue }
    }
    Pop-Location
}
