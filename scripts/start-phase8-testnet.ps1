[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composeArguments = @("compose", "--env-file", ".env.phase8.local", "-f", "compose.phase8.yaml")
$composeRuntimeArguments = $composeArguments + @("--profile", "runtime", "--profile", "testnet-gateway")

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "필요한 프로그램을 찾을 수 없습니다: $Name"
    }
}

try {
    Set-Location -LiteralPath $repoRoot
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host " Woozoo Binance Spot Testnet" -ForegroundColor Cyan
    Write-Host " 실제 Testnet 주문 / 기록 재생 데이터 / Mock AI / PAPER 모드" -ForegroundColor DarkCyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
    foreach ($commandName in @("docker", "node", "corepack", "python")) {
        Assert-Command $commandName
    }
    docker info --format "{{.ServerVersion}}" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop에 연결할 수 없습니다."
    }
    corepack pnpm bootstrap
    if ($LASTEXITCODE -ne 0) {
        throw "프로젝트 의존성 준비에 실패했습니다."
    }
    & (Join-Path $PSScriptRoot "setup-phase8-testnet.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Testnet secret 준비에 실패했습니다."
    }

    Write-Host ""
    Write-Host "[Woozoo] 격리된 Testnet 서비스를 빌드하고 시작합니다." -ForegroundColor Cyan
    & docker @composeRuntimeArguments up --build -d --wait
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 8 Docker 서비스 시작에 실패했습니다."
    }

    if ($env:WOOZOO_OPEN_BROWSER -ne "0") {
        $env:WOOZOO_OPEN_BROWSER = "1"
    }
    corepack pnpm testnet:local:start
    if ($LASTEXITCODE -ne 0) {
        throw "Testnet 로컬 화면 시작에 실패했습니다."
    }
}
catch {
    Write-Host ""
    Write-Host "[Woozoo] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "도움말: docs\TESTNET_QUICK_START_KO.md" -ForegroundColor Yellow
    exit 1
}
finally {
    Set-Location -LiteralPath $repoRoot
    if (Test-Path -LiteralPath (Join-Path $repoRoot ".env.phase8.local")) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker @composeRuntimeArguments down 2>&1 | Out-Null
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }
}

exit 0
