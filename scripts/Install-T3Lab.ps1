<#
.SYNOPSIS
    Installs / verifies the T3Lab pyRevit extension on any Windows machine.

.DESCRIPTION
    One script for the whole deployment story. It checks everything that has
    ever made T3Lab fail on a machine other than the developer's, then (unless
    -CheckOnly) registers the extension with pyRevit and clears stale caches.

    Checks, in order:
      1. pyRevit clone(s) — any name, any install root
      2. CPython engine   — any CPY3* build, reported with its Python version
      3. pyRevit CLI      — used for registration when present
      4. Revit versions installed, with their journal folders
      5. Extension folder — exists, writable, not a OneDrive cloud-only
                            placeholder, path length sane, files unblocked
      6. Duplicate T3Lab registrations (a stale copy shadows the real one)
      7. Optional features (Excel interop)

    Every finding prints as OK / WARN / FAIL. FAIL means a tool will throw
    "Command Failure for External Command"; WARN means a feature degrades.

.PARAMETER ExtensionPath
    Path to T3Lab.extension. Defaults to the copy next to this script.

.PARAMETER CheckOnly
    Report only — register nothing, delete nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Install-T3Lab.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Install-T3Lab.ps1 -CheckOnly
#>

[CmdletBinding()]
param(
    [string]$ExtensionPath,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$script:Failures = 0
$script:Warnings = 0

function Write-Head($text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}
function Write-Ok($text)   { Write-Host "  [ OK ]   $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [ WARN ] $text" -ForegroundColor Yellow; $script:Warnings++ }
function Write-Fail($text) { Write-Host "  [ FAIL ] $text" -ForegroundColor Red;    $script:Failures++ }
function Write-Info($text) { Write-Host "           $text" -ForegroundColor Gray }

# ─────────────────────────────────────────────────────────────────────────────
# pyRevit discovery — mirrors lib/_cpython_bootstrap.py, clone-name agnostic
# ─────────────────────────────────────────────────────────────────────────────

function Get-PyRevitClones {
    $roots = @()
    $bases = @($env:APPDATA, $env:PROGRAMDATA, $env:LOCALAPPDATA,
               $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432)
    foreach ($base in ($bases | Select-Object -Unique)) {
        if (-not $base) { continue }
        if (-not (Test-Path $base)) { continue }
        $dirs = Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "pyRevit*" }
        foreach ($d in $dirs) {
            if (Test-Path (Join-Path $d.FullName "bin")) { $roots += $d.FullName }
        }
    }
    return ($roots | Select-Object -Unique)
}

function Get-EngineVersion($engineDir, $engineName) {
    # The stdlib zip is authoritative: python312.zip -> 3.12
    $zip = Get-ChildItem -Path $engineDir -Filter "python*.zip" -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($zip) {
        $digits = ($zip.BaseName -replace '\D', '')
        if ($digits.Length -ge 2) {
            return "{0}.{1}" -f $digits.Substring(0, 1), $digits.Substring(1)
        }
    }
    # Fallback on the folder name: CPY3123 -> 3.12, CPY387 -> 3.8
    $digits = ($engineName -replace '\D', '')
    if ($digits.Length -ge 2) {
        $rest = $digits.Substring(1)
        if ($rest.Length -ge 3) { $minor = $rest.Substring(0, 2) } else { $minor = $rest.Substring(0, 1) }
        return "{0}.{1}" -f $digits.Substring(0, 1), $minor
    }
    return "?"
}

function Get-CPythonEngines($clones) {
    $found = @()
    foreach ($clone in $clones) {
        $ceng = Join-Path $clone "bin\cengines"
        if (-not (Test-Path $ceng)) { continue }
        $dirs = Get-ChildItem -Path $ceng -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "CPY*" }
        foreach ($d in $dirs) {
            $found += [pscustomobject]@{
                Path    = $d.FullName
                Name    = $d.Name
                Version = (Get-EngineVersion $d.FullName $d.Name)
            }
        }
    }
    return $found
}

# ─────────────────────────────────────────────────────────────────────────────

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  T3Lab extension - install / environment check" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# ── Extension folder ─────────────────────────────────────────────────────────
Write-Head "Extension folder"

if (-not $ExtensionPath) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $ExtensionPath = Join-Path $repoRoot "T3Lab.extension"
}
$ExtensionPath = $ExtensionPath.TrimEnd('\')

if (-not (Test-Path $ExtensionPath)) {
    Write-Fail "Not found: $ExtensionPath"
    Write-Info "Pass the real location with -ExtensionPath <folder>."
    exit 1
}
Write-Ok "$ExtensionPath"

if (-not (Test-Path (Join-Path $ExtensionPath "lib\_cpython_bootstrap.py"))) {
    Write-Fail "lib\_cpython_bootstrap.py is missing - this is not a complete T3Lab.extension."
}

# Path length: Revit + pyRevit still hit MAX_PATH on deep nested scripts.
$deepest = Get-ChildItem -Path $ExtensionPath -Recurse -File -ErrorAction SilentlyContinue |
           Sort-Object { $_.FullName.Length } -Descending | Select-Object -First 1
if ($deepest) {
    if ($deepest.FullName.Length -ge 250) {
        Write-Fail "Longest path is $($deepest.FullName.Length) chars (>= 250). Move the extension closer to the drive root, e.g. C:\T3Lab."
        Write-Info $deepest.FullName
    } elseif ($deepest.FullName.Length -ge 200) {
        Write-Warn "Longest path is $($deepest.FullName.Length) chars. Safe for now, but avoid nesting it any deeper."
    } else {
        Write-Ok "Longest path $($deepest.FullName.Length) chars"
    }
}

# Writable: chat history / tool registry are written back into lib\config.
$probe = Join-Path $ExtensionPath "lib\config\.t3lab_write_probe"
try {
    New-Item -ItemType Directory -Force -Path (Split-Path $probe) | Out-Null
    Set-Content -Path $probe -Value "probe" -Encoding utf8
    Remove-Item $probe -Force
    Write-Ok "Folder is writable"
} catch {
    Write-Warn "Folder is NOT writable - the assistant cannot save its tool registry or chat history."
    Write-Info "Install under a user-writable path instead of C:\Program Files."
}

# OneDrive cloud-only placeholders: the file exists but reading it fails offline.
$placeholders = Get-ChildItem -Path $ExtensionPath -Recurse -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Attributes.ToString() -match "Offline" }
if ($placeholders) {
    Write-Warn "$($placeholders.Count) file(s) are OneDrive cloud-only placeholders."
    Write-Info "Right-click the folder > 'Always keep on this device', or install outside OneDrive."
} else {
    Write-Ok "No cloud-only placeholder files"
}

# Mark of the Web: files extracted from a downloaded zip load as untrusted.
$blocked = Get-ChildItem -Path $ExtensionPath -Recurse -File -ErrorAction SilentlyContinue |
           Where-Object { Get-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue }
if ($blocked) {
    if ($CheckOnly) {
        Write-Warn "$($blocked.Count) file(s) are blocked by Windows (downloaded zip). Run without -CheckOnly to unblock."
    } else {
        $blocked | Unblock-File
        Write-Ok "Unblocked $($blocked.Count) downloaded file(s)"
    }
} else {
    Write-Ok "No files blocked by Windows"
}

# ── pyRevit ──────────────────────────────────────────────────────────────────
Write-Head "pyRevit"

$clones = Get-PyRevitClones
if (-not $clones) {
    Write-Fail "No pyRevit installation found."
    Write-Info "Install pyRevit first: https://github.com/pyrevitlabs/pyRevit/releases"
} else {
    foreach ($c in $clones) { Write-Ok "Clone: $c" }
}

$engines = Get-CPythonEngines $clones
if (-not $engines) {
    Write-Fail "No CPython engine (bin\cengines\CPY*) found in any pyRevit clone."
    Write-Info "T3Lab scripts start with '#! python3' and cannot run without it."
    Write-Info "Install a pyRevit build that ships CPython, or re-run the pyRevit installer."
} else {
    foreach ($e in $engines) { Write-Ok "CPython engine: $($e.Name) (Python $($e.Version)) - $($e.Path)" }
    $has312 = $engines | Where-Object { $_.Version -eq "3.12" }
    if (-not $has312) {
        Write-Warn "No Python 3.12 engine. T3Lab is developed against CPY3123 (Python 3.12)."
        Write-Info "Older engines (CPY387 = Python 3.8) may fail on newer syntax in some tools."
    }
}

$cli = Get-Command pyrevit -ErrorAction SilentlyContinue
if (-not $cli) {
    foreach ($c in $clones) {
        $candidate = Join-Path $c "bin\pyrevit.exe"
        if (Test-Path $candidate) { $cli = Get-Item $candidate; break }
    }
}
if ($cli) {
    if ($cli.Source) { $cliPath = $cli.Source } else { $cliPath = $cli.FullName }
    Write-Ok "pyRevit CLI: $cliPath"
} else {
    Write-Warn "pyRevit CLI (pyrevit.exe) not found - the extension must be registered from the pyRevit ribbon instead."
}

# ── Revit ────────────────────────────────────────────────────────────────────
Write-Head "Revit"

$revitYears = @()
$revitRoot = "$env:PROGRAMFILES\Autodesk"
if (Test-Path $revitRoot) {
    $revitYears = Get-ChildItem -Path $revitRoot -Directory -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -match "^Revit \d{4}$" } |
                  ForEach-Object { $_.Name.Substring($_.Name.Length - 4) }
}
if (-not $revitYears) {
    Write-Warn "No Revit installation detected under $revitRoot."
} else {
    foreach ($y in $revitYears) {
        Write-Ok "Revit $y"
        Write-Info "Journal: $env:LOCALAPPDATA\Autodesk\Revit\Autodesk Revit $y\Journals"
    }
}

# ── Duplicate registrations ──────────────────────────────────────────────────
Write-Head "Duplicate T3Lab copies"

$otherCopies = @()
$searchRoots = @("$env:APPDATA\pyRevit", "$env:APPDATA\pyRevit-Master",
                 "$env:PROGRAMDATA\pyRevit", "$env:USERPROFILE\Documents")
foreach ($r in ($searchRoots | Select-Object -Unique)) {
    if (-not (Test-Path $r)) { continue }
    $hits = Get-ChildItem -Path $r -Recurse -Directory -Filter "T3Lab.extension" -Depth 5 -ErrorAction SilentlyContinue
    foreach ($h in $hits) {
        if ($h.FullName -ne $ExtensionPath) { $otherCopies += $h.FullName }
    }
}
if ($otherCopies) {
    Write-Warn "Another T3Lab.extension exists on this machine:"
    foreach ($o in ($otherCopies | Select-Object -Unique)) { Write-Info $o }
    Write-Info "Two copies both publish a T3Lab tab and fight over sys.modules. Keep exactly one."
} else {
    Write-Ok "Only one T3Lab.extension found"
}

# ── Optional features ────────────────────────────────────────────────────────
Write-Head "Optional features"

$excel = $null
try { $excel = New-Object -ComObject Excel.Application } catch { $excel = $null }
if ($excel) {
    Write-Ok "Microsoft Excel available (IFC-SG, Parameter Manager, Sheet Manager import/export)"
    try { $excel.Quit() } catch { }
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
} else {
    Write-Warn "Microsoft Excel not installed - Excel import/export in IFC-SG / Parameter Manager / Sheet Manager will not work."
}

# ── Register ─────────────────────────────────────────────────────────────────
if (-not $CheckOnly) {
    Write-Head "Registering the extension with pyRevit"

    $parent = Split-Path -Parent $ExtensionPath
    if ($cli) {
        $exe = if ($cli.Source) { $cli.Source } else { $cli.FullName }
        try {
            & $exe extensions paths add "$parent"
            Write-Ok "Search path registered: $parent"
        } catch {
            Write-Warn "pyrevit CLI could not register the path: $($_.Exception.Message)"
            Write-Info "Add it by hand: pyRevit ribbon > Settings > Custom Extension Directories > $parent"
        }
    } else {
        Write-Warn "No pyRevit CLI - add the folder by hand."
        Write-Info "pyRevit ribbon > Settings > Custom Extension Directories > add: $parent"
    }

    Write-Head "Clearing stale caches"
    $revit = Get-Process | Where-Object { $_.ProcessName -like "*Revit*" }
    if ($revit) {
        Write-Warn "Revit is running - caches were NOT cleared. Close Revit and re-run, or use pyRevit > Reload."
    } else {
        $cleared = 0
        foreach ($c in $clones) {
            $patterns = @("$c\pyrevit\*cache*", "$c\pyrevit\*.dll", "$c\Extensions\*.dll")
            foreach ($p in $patterns) {
                $items = Get-ChildItem -Path $p -Recurse -ErrorAction SilentlyContinue
                foreach ($i in $items) {
                    try { Remove-Item $i.FullName -Force -Recurse -Confirm:$false; $cleared++ } catch { }
                }
            }
        }
        $tmp = Get-ChildItem -Path "$env:TEMP\pyRevit*" -ErrorAction SilentlyContinue
        foreach ($i in $tmp) {
            try { Remove-Item $i.FullName -Force -Recurse -Confirm:$false; $cleared++ } catch { }
        }
        Write-Ok "Removed $cleared cached item(s)"
    }
}

# ── Summary ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
if ($script:Failures -gt 0) {
    Write-Host "  RESULT: $($script:Failures) failure(s), $($script:Warnings) warning(s)" -ForegroundColor Red
    Write-Host "  Fix every FAIL above before opening Revit." -ForegroundColor Red
} elseif ($script:Warnings -gt 0) {
    Write-Host "  RESULT: ready, with $($script:Warnings) warning(s)" -ForegroundColor Yellow
} else {
    Write-Host "  RESULT: ready" -ForegroundColor Green
}
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next: start Revit, then pyRevit > Reload (required once so the" -ForegroundColor Gray
Write-Host "persistent CPython engine picks the extension up)." -ForegroundColor Gray
Write-Host "If a tool still fails, the bootstrap report is at" -ForegroundColor Gray
Write-Host "  $env:APPDATA\T3LabAI\bootstrap.log" -ForegroundColor Gray

if ($script:Failures -gt 0) { exit 1 } else { exit 0 }
