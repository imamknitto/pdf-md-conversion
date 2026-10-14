# Local dev runner: .\dev.ps1 [-Port 8379] [-ListenHost 127.0.0.1] [--reload]
# - Default port 8379 (override via $env:PORT atau -Port).
# - Override: .\dev.ps1 -Port 8100  atau  $env:PORT=8100; .\dev.ps1
# - Extra args diteruskan ke uvicorn, mis. .\dev.ps1 --reload
param(
  [int]$Port = 0,
  [string]$ListenHost = ""
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
  # Load .env (kalau ada) agar PDF_MD_CONVERTER_API_KEY dkk ikut kebaca.
  $envFile = Join-Path $PSScriptRoot ".env"
  if (Test-Path -LiteralPath $envFile) {
    foreach ($line in (Get-Content -LiteralPath $envFile)) {
      if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
      $kv = $line -split '=', 2
      $k = $kv[0].Trim()
      $v = $kv[1].Trim().Trim('"').Trim("'")
      if ($k) { Set-Item -Path "env:$k" -Value $v }
    }
  }

  if ($Port -eq 0) {
    $Port = if ($env:PORT) { [int]$env:PORT } else { 8379 }
  }
  if (-not $ListenHost) {
    $ListenHost = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
  }

  $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $py)) {
    $py = "python"
  }

  # workers 1: engine OCR singleton (lihat README).
  & $py -m uvicorn app.main:app --host $ListenHost --port $Port --workers 1 @args
}
finally {
  Pop-Location
}
