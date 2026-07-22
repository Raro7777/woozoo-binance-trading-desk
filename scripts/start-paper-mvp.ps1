[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$verifierPath = Join-Path $repoRoot ".secrets\operator.argon2id"

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "필요한 프로그램을 찾을 수 없습니다: $Name"
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    Write-Host ""
    Write-Host "[Woozoo] $Label" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 단계가 실패했습니다. (종료 코드: $LASTEXITCODE)"
    }
}

function New-LocalOperatorVerifier {
    $firstSecure = $null
    $secondSecure = $null
    $firstPointer = [IntPtr]::Zero
    $secondPointer = [IntPtr]::Zero
    $firstPlain = $null
    $secondPlain = $null
    $secretPath = Join-Path ([System.IO.Path]::GetTempPath()) ("woozoo-operator-{0}.txt" -f [Guid]::NewGuid())

    try {
        Write-Host ""
        Write-Host "[Woozoo] 최초 로그인 비밀번호를 만듭니다." -ForegroundColor Yellow
        Write-Host "입력한 문자는 화면에 표시되지 않으며 저장소나 로그에 남지 않습니다."
        $firstSecure = Read-Host "사용할 비밀번호" -AsSecureString
        $secondSecure = Read-Host "같은 비밀번호를 한 번 더 입력" -AsSecureString

        $firstPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($firstSecure)
        $secondPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secondSecure)
        $firstPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($firstPointer)
        $secondPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($secondPointer)

        if ([string]::IsNullOrWhiteSpace($firstPlain)) {
            throw "비밀번호는 비어 있을 수 없습니다."
        }
        if ($firstPlain -cne $secondPlain) {
            throw "두 비밀번호가 서로 다릅니다. 다시 실행하세요."
        }

        $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($secretPath, $firstPlain, $utf8WithoutBom)
        Invoke-Checked "로컬 로그인 verifier 생성" {
            python -m uv run --locked python scripts/bootstrap-local-operator.py `
                --secret-file $secretPath `
                --verifier-file $verifierPath
        }
    }
    finally {
        if (Test-Path -LiteralPath $secretPath) {
            Remove-Item -LiteralPath $secretPath -Force
        }
        $firstPlain = $null
        $secondPlain = $null
        if ($firstPointer -ne [IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($firstPointer)
        }
        if ($secondPointer -ne [IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secondPointer)
        }
        if ($null -ne $firstSecure) {
            $firstSecure.Dispose()
        }
        if ($null -ne $secondSecure) {
            $secondSecure.Dispose()
        }
    }
}

try {
    Set-Location -LiteralPath $repoRoot
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host " Woozoo Paper MVP - 안전한 로컬 실행" -ForegroundColor Cyan
    Write-Host " 기록 재생(Fixture) / Mock AI / PAPER 전용" -ForegroundColor DarkCyan
    Write-Host "============================================================" -ForegroundColor DarkCyan

    foreach ($commandName in @("docker", "node", "corepack", "python")) {
        Assert-Command $commandName
    }

    Invoke-Checked "Docker Desktop 연결 확인" { docker info --format "{{.ServerVersion}}" }
    Invoke-Checked "프로젝트 의존성 준비" { corepack pnpm bootstrap }
    Invoke-Checked "안전한 로컬 환경변수 준비" { corepack pnpm env:init }

    if (Test-Path -LiteralPath $verifierPath) {
        Write-Host ""
        Write-Host "[Woozoo] 기존 로그인 설정을 사용합니다." -ForegroundColor Green
    }
    else {
        New-LocalOperatorVerifier
    }

    Write-Host ""
    Write-Host "[Woozoo] 앱을 시작합니다. 준비되면 브라우저가 자동으로 열립니다." -ForegroundColor Green
    Write-Host "종료할 때는 이 창에서 Ctrl+C를 누르세요."
    if ($env:WOOZOO_OPEN_BROWSER -ne "0") {
        $env:WOOZOO_OPEN_BROWSER = "1"
    }
    corepack pnpm paper:mvp:start
    if ($LASTEXITCODE -ne 0) {
        throw "Paper MVP 실행이 실패했습니다. (종료 코드: $LASTEXITCODE)"
    }
}
catch {
    Write-Host ""
    Write-Host "[Woozoo] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "도움말: docs\QUICK_START_KO.md" -ForegroundColor Yellow
    exit 1
}
finally {
    Set-Location -LiteralPath $repoRoot
}

exit 0
