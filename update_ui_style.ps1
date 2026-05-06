$toolsDir = Join-Path $PSScriptRoot "T3Lab.extension\lib\GUI\Tools"
$skip = @("ExportManager.xaml", "ExportManagerTest.xaml")
$updated = 0
$total = 0

Write-Host "============================================================"
Write-Host "Updating XAML files to match BatchOut design system"
Write-Host "Source: $toolsDir"
Write-Host "============================================================"

foreach ($file in (Get-ChildItem $toolsDir -Filter "*.xaml" | Sort-Object Name)) {
    $total++
    if ($skip -contains $file.Name) {
        Write-Host "[SKIP] $($file.Name) - Reference file"
        continue
    }
    
    $content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
    $original = $content
    $changes = @()
    
    # 1. Minimize: &#x2500; (box drawings) → MDL2 &#xE921;
    if ($content -match '&#x2500;') {
        $content = $content -replace 'Text="&#x2500;"([^/]*?)FontSize="\d+"([^/]*?)Foreground="#7F8C8D"', 'Text="&#xE921;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"'
        $changes += "minimize(box)"
    }
    
    # 1b. Minimize: &#8212; (em-dash) → MDL2 &#xE921;
    if ($content -match '&#8212;') {
        $content = $content -replace 'Text="&#8212;"([^/]*?)FontSize="\d+"([^/]*?)Foreground="#7F8C8D"', 'Text="&#xE921;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"'
        $changes += "minimize(dash)"
    }
    
    # 2. Maximize: &#x2610; (ballot box) → MDL2 &#xE922;
    if ($content -match '&#x2610;') {
        $content = $content -replace 'Text="&#x2610;"([^/]*?)FontSize="\d+"([^/]*?)Foreground="#7F8C8D"', 'Text="&#xE922;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"'
        $changes += "maximize"
    }
    
    # 3. Close button Content="&#x2715;" → Content="&#xE8BB;"
    if ($content -match 'Content="&#x2715;"') {
        $content = $content -replace 'Content="&#x2715;"', 'Content="&#xE8BB;"'
        $changes += "close(content)"
    }
    
    # 4. Close TextBlock: Text="&#x2715;" with bold/size → MDL2
    if ($content -match 'Text="&#x2715;"') {
        $content = $content -replace 'Text="&#x2715;"([^<]*?)FontSize="\d+"([^<]*?)FontWeight="Bold"([^<]*?)Foreground="#7F8C8D"', 'Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"'
        # Also simpler pattern without FontWeight
        $content = $content -replace 'Text="&#x2715;" FontSize="\d+" Foreground="#7F8C8D"', 'Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"'
        $changes += "close(text)"
    }
    
    # 5. Path-drawn X close icon → MDL2 TextBlock
    if ($content -match '<Path Stroke="#7F8C8D"') {
        $content = $content -replace '<Path Stroke="#7F8C8D" StrokeThickness="[^"]*"\s+Data="M2,2 L10,10 M10,2 L2,10"\s+Width="\d+" Height="\d+" Stretch="None"\s+HorizontalAlignment="Center" VerticalAlignment="Center"/>', '<TextBlock Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"/>'
        $changes += "close(path)"
    }
    
    if ($content -ne $original) {
        [System.IO.File]::WriteAllText($file.FullName, $content, (New-Object System.Text.UTF8Encoding $true))
        Write-Host "[UPDATED] $($file.Name) - $($changes -join ', ')"
        $updated++
    } else {
        Write-Host "[SKIP] $($file.Name) - No patterns matched"
    }
}

Write-Host ""
Write-Host "============================================================"
Write-Host "Done: $updated / $total files updated"
Write-Host "============================================================"
