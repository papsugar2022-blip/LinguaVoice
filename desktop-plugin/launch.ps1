$ErrorActionPreference = 'Stop'
$installation = (Get-ItemProperty -LiteralPath 'HKCU:\Software\LinguaVoice' -Name InstallPath).InstallPath
$python = Join-Path $installation '.venv\Scripts\python.exe'
$entry = Join-Path $installation 'run_mcp.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $entry)) { throw 'Please install LinguaVoice first.' }
& $python $entry
exit $LASTEXITCODE
