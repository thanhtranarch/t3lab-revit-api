$dir = "c:\Users\tran_tienthanh\OneDrive - Woh Hup (Private) Limited\Documents\T3Lab Architect\t3lab-revit-api\T3Lab.extension\lib\GUI\Tools"
$ref = "ExportManager.xaml"

# Extract BatchOut palette
$refContent = Get-Content (Join-Path $dir $ref) -Raw
$refColors = [regex]::Matches($refContent, '#[0-9A-Fa-f]{6}') | ForEach-Object { $_.Value.ToUpper() } | Sort-Object -Unique

# Add accepted extended colors
$accepted = $refColors + @(
    '#1E8449',   # success hover green (close to #27AE60)
    '#C0392B',   # danger hover red (darker #E74C3C)
    '#E67E22',   # orange accent (used across tools)
    '#F39C12',   # yellow accent
    '#EBF5FB',   # light blue hover row
    '#D6EAF8',   # blue selected row
    '#F0F8FF',   # alice blue (selected card)
    '#95A5A6'    # medium gray (labels)
)

# Check each file
Get-ChildItem (Join-Path $dir "*.xaml") | ForEach-Object {
    $name = $_.Name
    $content = Get-Content $_.FullName -Raw
    $fileColors = [regex]::Matches($content, '#[0-9A-Fa-f]{6}') | ForEach-Object { $_.Value.ToUpper() } | Sort-Object -Unique
    $nonStandard = $fileColors | Where-Object { $accepted -notcontains $_ }
    if ($nonStandard) {
        Write-Host "=== $name ==="
        Write-Host "  Non-standard: $($nonStandard -join ', ')"
    }
}
