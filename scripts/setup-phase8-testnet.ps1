[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$secretRoot = Join-Path $repoRoot ".secrets\phase8"
$environmentPath = Join-Path $repoRoot ".env.phase8.local"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )
    [System.IO.File]::WriteAllText($Path, $Value, $utf8WithoutBom)
}

function Protect-SecretFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $security = New-Object System.Security.AccessControl.FileSecurity
    $security.SetOwner($identity.User)
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $identity.User,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $security.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $security
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $generator.Dispose()
    }
}

function Ensure-RandomSecretFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Utf8File -Path $Path -Value ((New-RandomSecret) + "`n")
        Protect-SecretFile -Path $Path
    }
}

function Read-HiddenAsciiLine {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [IntPtr]::Zero
    $plain = $null
    try {
        $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain -match "[\r\n]" -or $plain -notmatch "^[\x21-\x7E]+$") {
            throw "값은 공백 없는 ASCII 한 줄이어야 합니다."
        }
        return $plain
    }
    finally {
        $plain = $null
        if ($pointer -ne [IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        $secure.Dispose()
    }
}

function Ensure-ExchangeSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Prompt
    )

    if (Test-Path -LiteralPath $Path) {
        return
    }
    $value = Read-HiddenAsciiLine -Prompt $Prompt
    try {
        Write-Utf8File -Path $Path -Value ($value + "`n")
        Protect-SecretFile -Path $Path
    }
    finally {
        $value = $null
    }
}

function Ensure-OperatorVerifier {
    param([Parameter(Mandatory = $true)][string]$VerifierPath)

    if (Test-Path -LiteralPath $VerifierPath) {
        return
    }
    $first = Read-HiddenAsciiLine -Prompt "Woozoo 로그인 비밀번호"
    $second = Read-HiddenAsciiLine -Prompt "같은 로그인 비밀번호 다시 입력"
    if ($first -cne $second) {
        throw "로그인 비밀번호가 서로 다릅니다."
    }
    $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("woozoo-phase8-login-{0}.secret" -f [Guid]::NewGuid())
    try {
        Write-Utf8File -Path $temporary -Value ($first + "`n")
        python -m uv run --locked python scripts/bootstrap-local-operator.py `
            --secret-file $temporary `
            --verifier-file $VerifierPath
        if ($LASTEXITCODE -ne 0) {
            throw "로컬 로그인 verifier 생성에 실패했습니다."
        }
        Protect-SecretFile -Path $VerifierPath
    }
    finally {
        $first = $null
        $second = $null
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

Set-Location -LiteralPath $repoRoot
New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null

$roleFiles = [ordered]@{
    PHASE8_POSTGRES_SUPERUSER_PASSWORD_FILE = "postgres-superuser.secret"
    PHASE8_CONTROL_READER_DATABASE_PASSWORD_FILE = "control-reader.secret"
    PHASE8_CONTROL_API_DATABASE_PASSWORD_FILE = "control-api.secret"
    PHASE8_MARKET_WRITER_DATABASE_PASSWORD_FILE = "market-writer.secret"
    PHASE8_EVIDENCE_WRITER_DATABASE_PASSWORD_FILE = "evidence-writer.secret"
    PHASE8_AGENT_ORCHESTRATOR_DATABASE_PASSWORD_FILE = "agent-orchestrator.secret"
    PHASE8_RISK_ENGINE_DATABASE_PASSWORD_FILE = "risk-engine.secret"
    PHASE8_PAPER_ENGINE_DATABASE_PASSWORD_FILE = "paper-engine.secret"
    PHASE8_TESTNET_EXECUTION_DATABASE_PASSWORD_FILE = "testnet-execution.secret"
    PHASE8_SPOT_TESTNET_GATEWAY_DATABASE_PASSWORD_FILE = "spot-testnet-gateway.secret"
}
foreach ($fileName in $roleFiles.Values) {
    Ensure-RandomSecretFile -Path (Join-Path $secretRoot $fileName)
}

$verifierPath = Join-Path $secretRoot "operator.argon2id"
$apiKeyPath = Join-Path $secretRoot "spot-testnet-api-key.secret"
$signingSecretPath = Join-Path $secretRoot "spot-testnet-signing-secret.secret"
$instancePath = Join-Path $secretRoot "gateway-instance-id.txt"

Ensure-OperatorVerifier -VerifierPath $verifierPath
Write-Host ""
Write-Host "Binance Spot Testnet 전용 키를 입력하세요." -ForegroundColor Yellow
Write-Host "값은 화면, 로그, .env 파일에 표시되지 않습니다. 실거래용 키는 입력하지 마세요."
Ensure-ExchangeSecret -Path $apiKeyPath -Prompt "Spot Testnet API Key"
Ensure-ExchangeSecret -Path $signingSecretPath -Prompt "Spot Testnet Secret Key"

if (-not (Test-Path -LiteralPath $instancePath)) {
    $instanceBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($instanceBytes)
        Write-Utf8File -Path $instancePath -Value (([BitConverter]::ToString($instanceBytes).Replace('-', '').ToLowerInvariant()) + "`n")
        Protect-SecretFile -Path $instancePath
    }
    finally {
        [Array]::Clear($instanceBytes, 0, $instanceBytes.Length)
        $generator.Dispose()
    }
}

$metadataText = python -m uv run --locked python scripts/phase8-runtime-metadata.py
if ($LASTEXITCODE -ne 0) {
    throw "Phase 8 런타임 메타데이터 계산에 실패했습니다."
}
$metadata = $metadataText | ConvertFrom-Json
$instanceId = ([System.IO.File]::ReadAllText($instancePath)).Trim()

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("TRADING_MODE=paper")
$lines.Add("MARKET_DATA_SOURCE=recorded")
$lines.Add("LLM_PROVIDER=mock")
$lines.Add("LOCAL_OPERATOR_ORIGIN=https://localhost:3443")
$lines.Add("LOCAL_OPERATOR_VERIFIER_FILE=.secrets/phase8/operator.argon2id")
$lines.Add("PHASE8_POSTGRES_PORT=5438")
$lines.Add("PHASE8_CONTROL_API_PORT=8008")
$lines.Add("SPOT_TESTNET_GATEWAY_ENABLED=true")
$lines.Add("SPOT_TESTNET_ENVIRONMENT=BINANCE_SPOT_TESTNET")
$lines.Add("SPOT_TESTNET_REST_ORIGIN=https://testnet.binance.vision")
$lines.Add("SPOT_TESTNET_WS_URL=wss://ws-api.testnet.binance.vision/ws-api/v3")
$lines.Add("SPOT_TESTNET_API_KEY_FILE=.secrets/phase8/spot-testnet-api-key.secret")
$lines.Add("SPOT_TESTNET_SIGNING_SECRET_FILE=.secrets/phase8/spot-testnet-signing-secret.secret")
$lines.Add("SPOT_TESTNET_GATEWAY_INSTANCE_ID=$instanceId")
$lines.Add("SPOT_TESTNET_GATEWAY_BUILD_DIGEST=$($metadata.build_digest)")
$lines.Add("SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST=$($metadata.configuration_digest)")
$lines.Add("SPOT_TESTNET_ALLOWLIST_DIGEST=$($metadata.allowlist_digest)")
foreach ($entry in $roleFiles.GetEnumerator()) {
    $lines.Add("$($entry.Key)=.secrets/phase8/$($entry.Value)")
}
Write-Utf8File -Path $environmentPath -Value (($lines -join "`n") + "`n")

Write-Host ""
Write-Host "Phase 8 로컬 secret과 환경 파일 준비가 끝났습니다." -ForegroundColor Green
Write-Host "거래소 키 값은 Gateway 전용 파일에만 저장됐습니다."
