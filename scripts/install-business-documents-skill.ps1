param(
    [string]$HermesHome = "$env:LOCALAPPDATA\hermes"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "hermes-skills\productivity\business-documents"
$target = Join-Path $HermesHome "skills\productivity\business-documents"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Source skill not found: $source"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $source "SKILL.md") -Destination (Join-Path $target "SKILL.md") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $target "scripts") | Out-Null
Copy-Item -LiteralPath (Join-Path $source "scripts\business_docs.py") -Destination (Join-Path $target "scripts\business_docs.py") -Force

Write-Host "Installed business-documents skill to $target"
