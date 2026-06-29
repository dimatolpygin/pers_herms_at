param(
    [string]$HermesHome = "$env:LOCALAPPDATA\hermes"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "hermes-skills\marketing\content-studio"
$target = Join-Path $HermesHome "skills\marketing\content-studio"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Source skill not found: $source"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $source "SKILL.md") -Destination (Join-Path $target "SKILL.md") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $target "scripts") | Out-Null
Copy-Item -LiteralPath (Join-Path $source "scripts\content_studio.py") -Destination (Join-Path $target "scripts\content_studio.py") -Force

Write-Host "Installed content-studio skill to $target"
