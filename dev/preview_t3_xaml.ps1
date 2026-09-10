# Render a T3 tool XAML to PNG without Revit.
#
# T3 XAMLs carry their whole stylesheet inline, so unlike the old Revit-theme
# renderer this needs no palette file: load, strip the event handlers XamlReader
# cannot resolve without a code-behind type, lay out off-screen, snapshot.
param(
  [string]$Xaml,
  [string]$Out,
  [int]$W = 1080,
  [int]$H = 720,
  [int]$Tab = -1
)

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$ErrorActionPreference = 'Stop'
$src = Get-Content -Raw -Encoding UTF8 $Xaml

$events = 'Click|Checked|Unchecked|SelectionChanged|TextChanged|SizeChanged|' +
          'MouseWheel|MouseDoubleClick|MouseLeftButtonDown|MouseLeftButtonUp|' +
          'MouseMove|MouseLeave|MouseEnter|CellEditEnding|LostFocus|GotFocus|' +
          'Loaded|KeyDown|KeyUp|PreviewKeyDown|Closing|Closed|StateChanged|' +
          'RequestNavigate|Drop|DragOver|Expanded|Collapsed'
$src = [regex]::Replace($src, "\s($events)=`"[^`"]*`"", '')

$reader = New-Object System.IO.StringReader($src)
$xr     = [System.Xml.XmlReader]::Create($reader)
$win    = [Windows.Markup.XamlReader]::Load($xr)
Write-Host "PARSED OK"

if ($Tab -ge 0) {
  $tc = $win.FindName('tab_control')
  if ($tc) { $tc.SelectedIndex = $Tab; Write-Host "tab_control -> $Tab" }
}

# detach content so it can lay out off-screen, carrying the window resources
$content = $win.Content
$win.Content = $null
$shell = New-Object Windows.Controls.Border
$shell.Resources = $win.Resources
$shell.Child = $content
$shell.Background = $win.Background
[Windows.Documents.TextElement]::SetFontFamily($shell, (New-Object Windows.Media.FontFamily 'Segoe UI'))
[Windows.Documents.TextElement]::SetFontSize($shell, 13)

$size = New-Object Windows.Size($W, $H)
# star columns and nested grids need several passes before they settle
for ($i = 0; $i -lt 6; $i++) {
  $shell.Measure($size)
  $shell.Arrange((New-Object Windows.Rect(0, 0, $W, $H)))
  $shell.UpdateLayout()
  [Windows.Threading.Dispatcher]::CurrentDispatcher.Invoke(
    [Windows.Threading.DispatcherPriority]::SystemIdle, [action]{}) | Out-Null
}

$rtb = New-Object Windows.Media.Imaging.RenderTargetBitmap($W, $H, 96, 96,
        [Windows.Media.PixelFormats]::Pbgra32)
$rtb.Render($shell)
$enc = New-Object Windows.Media.Imaging.PngBitmapEncoder
$enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($rtb)) | Out-Null
$fs = [IO.File]::Create($Out)
$enc.Save($fs)
$fs.Close()
Write-Host "WROTE $Out"
