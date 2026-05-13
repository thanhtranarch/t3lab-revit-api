# WPF Window Templates

> **Reference**: All templates below match `BulkFamilyExport.xaml` — the canonical UI standard.

## Window Structure

Every tool window must include:

### 1. Window Root + WindowChrome
```xml
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="T3Lab - Tool Name"
        Width="1100" Height="680"
        MinWidth="860" MinHeight="500"
        Background="White"
        ResizeMode="CanResizeWithGrip"
        WindowStartupLocation="CenterScreen"
        FontFamily="Inter">

    <WindowChrome.WindowChrome>
        <WindowChrome CaptionHeight="64"
                      ResizeBorderThickness="5"
                      GlassFrameThickness="0"
                      CornerRadius="8"
                      UseAeroCaptionButtons="False"/>
    </WindowChrome.WindowChrome>
```

### 2. Title Bar Row (64px, white)
```xml
<Grid Grid.Row="0" Height="64" Background="White">
    <StackPanel Orientation="Vertical" Margin="16,0,0,0" VerticalAlignment="Center"
                WindowChrome.IsHitTestVisibleInChrome="True">
        <StackPanel Orientation="Horizontal">
            <TextBlock Text="T3Lab" FontSize="11" FontWeight="Bold" Foreground="#083D56"
                       VerticalAlignment="Bottom" Margin="0,0,6,3"/>
            <TextBlock Text="Tool Name" FontSize="18" FontWeight="Bold"
                       Foreground="#2C3E50"/>
        </StackPanel>
        <Separator Height="1" Background="#546E7A" Margin="0,2,0,2"/>
        <TextBlock Text="Short description of the tool"
                   FontSize="10" Foreground="#7F8C8D" FontStyle="Italic"/>
    </StackPanel>

    <!-- Right: Window control buttons -->
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Top"
                WindowChrome.IsHitTestVisibleInChrome="True">
        <Button x:Name="btn_minimize" Style="{StaticResource WinCtrlButton}"
                Click="minimize_button_clicked" ToolTip="Minimize">
            <TextBlock Text="&#xE921;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#083D56"/>
        </Button>
        <Button x:Name="btn_maximize" Style="{StaticResource WinCtrlButton}"
                Click="maximize_button_clicked" ToolTip="Maximize">
            <TextBlock Text="&#xE922;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#083D56"/>
        </Button>
        <Button x:Name="btn_close" Style="{StaticResource CloseButton}"
                Click="close_button_clicked" ToolTip="Close">
            <TextBlock Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#083D56"/>
        </Button>
    </StackPanel>

    <Border Height="1" VerticalAlignment="Bottom" Background="#546E7A"/>
</Grid>
```

### 3. Status Bar Row
```xml
<Border Grid.Row="N" Background="#F8F9FA" BorderBrush="#546E7A" BorderThickness="0,1,0,0"
        Padding="14,8">
    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="status_text" Grid.Column="0" FontSize="11"
                   Foreground="#7F8C8D" Text="Ready"/>
        <TextBlock x:Name="count_text" Grid.Column="1" FontSize="11"
                   Foreground="#546E7A" Text="0 items"/>
    </Grid>
</Border>
```

### 4. Copyright (always before closing root Grid)
```xml
    <!-- Copyright added automatically -->
    <TextBlock Text="© Copyright by T3Lab" HorizontalAlignment="Right" VerticalAlignment="Bottom" Margin="0,0,14,8" Foreground="#3498DB" FontSize="11" IsHitTestVisible="False" Panel.ZIndex="999"/>
</Grid>
```

---

## Button Styles

Define these as `Window.Resources`:

```xml
<!-- PRIMARY BUTTON - blue, white text -->
<Style x:Key="PrimaryButton" TargetType="Button">
    <Setter Property="Background"      Value="#083D56"/>
    <Setter Property="Foreground"      Value="White"/>
    <Setter Property="Padding"         Value="12,6"/>
    <Setter Property="FontSize"        Value="12"/>
    <Setter Property="FontFamily"      Value="Inter"/>
    <Setter Property="Cursor"          Value="Hand"/>
    <Setter Property="BorderThickness" Value="0"/>
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border Background="{TemplateBinding Background}" CornerRadius="3"
                        Padding="{TemplateBinding Padding}">
                    <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#062A3C"/>
        </Trigger>
        <Trigger Property="IsEnabled" Value="False">
            <Setter Property="Background" Value="#546E7A"/>
            <Setter Property="Cursor"     Value="Arrow"/>
        </Trigger>
    </Style.Triggers>
</Style>

<!-- SECONDARY BUTTON - light gray, dark text -->
<Style x:Key="SecondaryButton" TargetType="Button">
    <Setter Property="Background"      Value="#F8F9FA"/>
    <Setter Property="Foreground"      Value="#2C3E50"/>
    <Setter Property="Padding"         Value="12,6"/>
    <Setter Property="FontSize"        Value="12"/>
    <Setter Property="FontFamily"      Value="Inter"/>
    <Setter Property="Cursor"          Value="Hand"/>
    <Setter Property="BorderThickness" Value="1"/>
    <Setter Property="BorderBrush"     Value="#546E7A"/>
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border Background="{TemplateBinding Background}"
                        BorderBrush="{TemplateBinding BorderBrush}"
                        BorderThickness="{TemplateBinding BorderThickness}"
                        CornerRadius="3" Padding="{TemplateBinding Padding}">
                    <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#E2E6EA"/>
        </Trigger>
    </Style.Triggers>
</Style>

<!-- SUCCESS BUTTON - green -->
<Style x:Key="SuccessButton" TargetType="Button" BasedOn="{StaticResource PrimaryButton}">
    <Setter Property="Background" Value="#27AE60"/>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#1E8449"/>
        </Trigger>
    </Style.Triggers>
</Style>

<!-- DANGER BUTTON - red (delete/destructive) -->
<Style x:Key="DangerButton" TargetType="Button" BasedOn="{StaticResource PrimaryButton}">
    <Setter Property="Background" Value="#D32F2F"/>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#B71C1C"/>
        </Trigger>
    </Style.Triggers>
</Style>

<!-- WINDOW CONTROL BUTTON -->
<Style x:Key="WinCtrlButton" TargetType="Button">
    <Setter Property="Width"           Value="40"/>
    <Setter Property="Height"          Value="32"/>
    <Setter Property="Background"      Value="Transparent"/>
    <Setter Property="BorderThickness" Value="0"/>
    <Setter Property="Cursor"          Value="Hand"/>
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border x:Name="bd" Background="{TemplateBinding Background}">
                    <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
    <Style.Triggers>
        <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#F8F9FA"/>
        </Trigger>
    </Style.Triggers>
</Style>

<!-- CLOSE BUTTON - red on hover -->
<Style x:Key="CloseButton" TargetType="Button" BasedOn="{StaticResource WinCtrlButton}">
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border x:Name="bd" Background="{TemplateBinding Background}">
                    <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                </Border>
                <ControlTemplate.Triggers>
                    <Trigger Property="IsMouseOver" Value="True">
                        <Setter TargetName="bd" Property="Background" Value="#D32F2F"/>
                    </Trigger>
                </ControlTemplate.Triggers>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
</Style>
```

---

## DataGrid Style

```xml
<DataGrid Background="White" BorderBrush="#546E7A" BorderThickness="1"
          AlternatingRowBackground="#F8F9FA" FontFamily="Inter" FontSize="12"
          HeadersVisibility="Column" GridLinesVisibility="Horizontal"
          HorizontalGridLinesBrush="#F8F9FA">
    <DataGrid.ColumnHeaderStyle>
        <Style TargetType="DataGridColumnHeader">
            <Setter Property="Background"      Value="#F8F9FA"/>
            <Setter Property="Foreground"      Value="#2C3E50"/>
            <Setter Property="FontWeight"      Value="SemiBold"/>
            <Setter Property="Padding"         Value="8,6"/>
            <Setter Property="BorderBrush"     Value="#546E7A"/>
            <Setter Property="BorderThickness" Value="0,0,1,1"/>
            <Setter Property="Height"          Value="34"/>
        </Style>
    </DataGrid.ColumnHeaderStyle>
    <DataGrid.RowStyle>
        <Style TargetType="DataGridRow">
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="#F8F9FA"/>
                </Trigger>
                <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="#E2E6EA"/>
                </Trigger>
            </Style.Triggers>
        </Style>
    </DataGrid.RowStyle>
</DataGrid>
```

---

## Info / Tip Box

```xml
<Border BorderBrush="#083D56" BorderThickness="1" Background="#F8F9FA"
        CornerRadius="2" Padding="10">
    <StackPanel Orientation="Horizontal">
        <TextBlock Text="Tip:" FontWeight="Bold" Foreground="#062A3C" Margin="0,0,5,0"/>
        <TextBlock Text="Your message here." Foreground="#2C3E50" TextWrapping="Wrap"/>
    </StackPanel>
</Border>
```

---

## Progress Bar (for long-running tasks)

```xml
<!-- Place inside Status Bar, Visibility="Collapsed" when idle -->
<Grid x:Name="progress_panel" Visibility="Collapsed" Margin="0,0,0,6">
    <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="8"/>
        <ColumnDefinition Width="64"/>
        <ColumnDefinition Width="4"/>
        <ColumnDefinition Width="52"/>
    </Grid.ColumnDefinitions>

    <ProgressBar x:Name="pb_export" Grid.Column="0"
                 Height="8" Minimum="0" Maximum="100" Value="0"
                 Foreground="#083D56" Background="#E2E6EA"
                 BorderThickness="0" VerticalAlignment="Center"/>

    <!-- Pause / Resume button -->
    <Button x:Name="btn_pause_export" Grid.Column="2"
            Content="⏸  Pause" Height="22" FontSize="10" FontFamily="Inter"
            Click="pause_resume_clicked" Cursor="Hand"
            BorderThickness="1" BorderBrush="#546E7A" VerticalAlignment="Center">
        <!-- Use inline secondary-like style -->
    </Button>

    <!-- Stop button -->
    <Button x:Name="btn_stop_export" Grid.Column="4"
            Content="■  Stop" Height="22" FontSize="10" FontFamily="Inter"
            Click="stop_export_clicked" Cursor="Hand"
            BorderThickness="0" VerticalAlignment="Center">
        <!-- Use inline danger-like style: Bg=#D32F2F, hover=#B71C1C -->
    </Button>
</Grid>
```
