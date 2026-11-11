# ProofArena - set the environment for this PowerShell window.
#
# Dot-source it so the variables land in your current session:
#
#   . .\scripts\testnet\set_env.ps1                 # testnet, existing account
#   . .\scripts\testnet\set_env.ps1 -Local          # local simulator instead
#   . .\scripts\testnet\set_env.ps1 -Account "my-name.testnet" -CredentialPath "C:\path\creds.json"
#
# Variables are set only for this window. Close it (or `Remove-Item Env:*`) to clear.
# No secret is read, printed, or stored by this script - it only points at the key file.

param(
    [string]$Account = "proofarena-c23922.testnet",
    [string]$CredentialPath,
    [switch]$Local,
    [switch]$Quiet
)

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $CredentialPath) {
    # Standard NEAR location first; the old in-repo fallback is kept only so an
    # existing setup keeps working. Credentials should NOT live inside the repo.
    $standard = Join-Path $HOME ".near-credentials\testnet\$Account.json"
    if (Test-Path -LiteralPath $standard) {
        $CredentialPath = $standard
    } else {
        $CredentialPath = Join-Path $repoRoot "evidence\near-credentials\$Account.json"
    }
}

if ($Local) {
    $env:PROOFARENA_CHAIN_MODE = "local"
    Remove-Item Env:NEAR_TESTNET_ACCOUNT -ErrorAction SilentlyContinue
    Remove-Item Env:NEAR_CONTRACT_ID -ErrorAction SilentlyContinue
    if (-not $Quiet) {
        Write-Host "CHAIN MODE : local simulator (no real transactions)" -ForegroundColor Yellow
        Write-Host "Next       : python scripts/dev.py   -> http://127.0.0.1:8787"
    }
    return
}

$env:PROOFARENA_CHAIN_MODE = "near-testnet"
$env:NEAR_TESTNET_ACCOUNT = $Account
$env:NEAR_CONTRACT_ID = $Account
$env:NEAR_RPC_URL = "https://rpc.testnet.near.org"
$env:PROOFARENA_SIGNER_URL = "http://127.0.0.1:8788"
$env:PROOFARENA_AUTH_DOMAIN = "proofarena.local"

$credentialStatus = "MISSING"
if (Test-Path -LiteralPath $CredentialPath) {
    $env:NEAR_CREDENTIALS = (Resolve-Path -LiteralPath $CredentialPath).Path
    $credentialStatus = "found (key not displayed)"
} else {
    $standard = Join-Path $HOME ".near-credentials\testnet\$Account.json"
    if (Test-Path -LiteralPath $standard) {
        $env:NEAR_CREDENTIALS = $standard
        $credentialStatus = "found at standard location"
    } else {
        Remove-Item Env:NEAR_CREDENTIALS -ErrorAction SilentlyContinue
        Write-Host "WARNING: no credentials file. Looked in:" -ForegroundColor Red
        Write-Host "  $CredentialPath"
        Write-Host "  $standard"
        Write-Host "Create one with: python scripts/testnet/create_account.py --name $Account" -ForegroundColor Yellow
    }
}

if (-not $Quiet) {
    Write-Host ""
    Write-Host "NEAR testnet environment (this PowerShell window only)" -ForegroundColor Cyan
    Write-Host "  account    : $env:NEAR_TESTNET_ACCOUNT"
    Write-Host "  contract   : $env:NEAR_CONTRACT_ID"
    Write-Host "  rpc        : $env:NEAR_RPC_URL"
    Write-Host "  chain mode : $env:PROOFARENA_CHAIN_MODE"
    Write-Host "  credentials: $credentialStatus"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  python scripts/testnet/preflight.py        # what is missing?"
    Write-Host "  cargo test --manifest-path contracts/escrow/Cargo.toml"
    Write-Host "  python scripts/testnet/deploy.py --dry-run"
    Write-Host "  python scripts/testnet/deploy.py --yes"
    Write-Host ""
    Write-Host "To clear: Remove-Item Env:NEAR_*, Env:PROOFARENA_* -ErrorAction SilentlyContinue"
}
