# Load every tool XAML through the real WPF XamlReader, the way pyRevit does.
# XML well-formedness is not enough: unknown members, duplicate resource keys and
# bad attached-property syntax only surface here, and in Revit they crash the
# tool the moment somebody clicks the button.
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$events = 'Click|Checked|Unchecked|SelectionChanged|TextChanged|SizeChanged|' +
          'MouseWheel|MouseDoubleClick|MouseLeftButtonDown|MouseLeftButtonUp|' +
          'MouseMove|MouseLeave|MouseEnter|CellEditEnding|LostFocus|GotFocus|' +
          'Loaded|KeyDown|KeyUp|PreviewKeyDown|PreviewMouseDown|PreviewMouseWheel|' +
          'Closing|Closed|StateChanged|RequestNavigate|Drop|DragOver|DragEnter|' +
          'Expanded|Collapsed|ValueChanged|Scroll|SourceUpdated|TargetUpdated|' +
          'PreviewTextInput|SelectedDateChanged|Sorting|BeginningEdit|' +
          'PreviewKeyUp|ContextMenuOpening|Initialized|Unloaded|ContentRendered|' +
          'PreviewMouseLeftButtonDown|PreviewMouseLeftButtonUp|PreviewMouseMove|' +
          'TextInput|Activated|Deactivated|LayoutUpdated|IsVisibleChanged'

$ok = 0
$failures = @()
foreach ($file in Get-ChildItem "T3Lab.extension\lib\GUI\Tools\*.xaml") {
  $name = $file.Name
  $src = Get-Content -Raw -Encoding UTF8 $file.FullName
  $src = [regex]::Replace($src, "\s($events)=`"[^`"]*`"", '')
  try {
    $reader = New-Object System.IO.StringReader($src)
    $xr = [System.Xml.XmlReader]::Create($reader)
    [Windows.Markup.XamlReader]::Load($xr) | Out-Null
    $ok++
  } catch {
    $msg = ($_.Exception.Message -replace "`r?`n", ' ')
    $failures += [pscustomobject]@{ File = $name; Error = $msg }
  }
}

foreach ($f in $failures) {
  Write-Host ("FAIL  " + $f.File)
  Write-Host ("      " + $f.Error)
}
Write-Host ""
Write-Host ("WPF parse: {0} OK, {1} FAILED" -f $ok, $failures.Count)
