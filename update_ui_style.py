"""
Batch update XAML title bar icons from Unicode chars to Segoe MDL2 Assets glyphs,
matching the BatchOut (ExportManager.xaml) design system.
"""
import os, re

TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "T3Lab.extension", "lib", "GUI", "Tools"
)

# Files already using the correct style (skip them)
SKIP = {"ExportManager.xaml", "ExportManagerTest.xaml"}

# The standardized close button with DataTrigger that turns icon white on hover
CLOSE_BUTTON_MDL2 = '''                <Button x:Name="btn_close"
                        Style="{{StaticResource CloseButton}}"
                        ToolTip="Close"
                        Click="{handler}">
                    <TextBlock x:Name="close_text" Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82">
                        <TextBlock.Style>
                            <Style TargetType="TextBlock">
                                <Style.Triggers>
                                    <DataTrigger Binding="{{Binding IsMouseOver, RelativeSource={{RelativeSource AncestorType=Button}}}}" Value="True">
                                        <Setter Property="Foreground" Value="White"/>
                                    </DataTrigger>
                                </Style.Triggers>
                            </Style>
                        </TextBlock.Style>
                    </TextBlock>
                </Button>'''

def process_file(filepath):
    """Process a single XAML file."""
    fname = os.path.basename(filepath)
    if fname in SKIP:
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    changes = []
    
    # --- 1. Replace minimize button icons ---
    # Pattern: Text="&#x2500;" or Text="&#8212;" (em-dash) in minimize buttons
    # Replace with MDL2 minimize glyph
    old_patterns_min = [
        r'Text="&#x2500;"([^/]*?)FontSize="12"([^/]*?)Foreground="#7F8C8D"',
        r'Text="&#8212;"([^/]*?)FontSize="12"([^/]*?)Foreground="#7F8C8D"',
    ]
    for pat in old_patterns_min:
        if re.search(pat, content):
            content = re.sub(pat, 
                r'Text="&#xE921;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"', 
                content)
            changes.append("minimize icon")
    
    # --- 2. Replace maximize button icons ---
    # Pattern: Text="&#x2610;" (ballot box)
    old_patterns_max = [
        r'Text="&#x2610;"([^/]*?)FontSize="12"([^/]*?)Foreground="#7F8C8D"',
    ]
    for pat in old_patterns_max:
        if re.search(pat, content):
            content = re.sub(pat,
                r'Text="&#xE922;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"',
                content)
            changes.append("maximize icon")
    
    # --- 3. Replace close button icons (simple cases without DataTrigger) ---
    # Various patterns for close icons using ✕ (&#x2715;)
    # Simple Content="&#x2715;" on buttons
    content = content.replace(
        'Content="&#x2715;"',
        'Content="&#xE8BB;"'
    )
    
    # TextBlock close icons - change to MDL2
    old_close_patterns = [
        (r'Text="&#x2715;"([^<]*?)FontSize="\d+"([^<]*?)FontWeight="Bold"([^<]*?)Foreground="#7F8C8D"',
         r'Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"'),
    ]
    for pat, repl in old_close_patterns:
        if re.search(pat, content):
            content = re.sub(pat, repl, content)
            changes.append("close icon")
    
    # --- 4. Replace Path-drawn X icons with MDL2 ---
    # Pattern in AlignPositions.xaml and others
    path_x_pattern = r'<Path Stroke="#7F8C8D" StrokeThickness="[^"]*"\s+Data="M2,2 L10,10 M10,2 L2,10"[^/]*/>'
    if re.search(path_x_pattern, content):
        content = re.sub(path_x_pattern,
            '<TextBlock Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#005B82"/>',
            content)
        changes.append("path close icon")
    
    # --- 5. Update WinCtrlButton style if it uses CornerRadius ---
    # The BatchOut style doesn't use CornerRadius on window control buttons
    # (This is a minor visual consistency fix)
    
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print("[UPDATED] {} - Changes: {}".format(fname, ", ".join(changes)))
        return True
    else:
        print("[SKIP] {} - No matching patterns found".format(fname))
        return False


def main():
    print("=" * 60)
    print("Updating XAML files to match BatchOut design system")
    print("Source directory: {}".format(TOOLS_DIR))
    print("=" * 60)
    
    updated = 0
    total = 0
    
    for fname in sorted(os.listdir(TOOLS_DIR)):
        if not fname.endswith('.xaml'):
            continue
        total += 1
        fpath = os.path.join(TOOLS_DIR, fname)
        if process_file(fpath):
            updated += 1
    
    print("\n" + "=" * 60)
    print("Done: {} / {} files updated".format(updated, total))
    print("=" * 60)


if __name__ == "__main__":
    main()
