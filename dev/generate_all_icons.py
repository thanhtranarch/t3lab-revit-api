# -*- coding: utf-8 -*-
"""
Procedural Icon Generator for T3Lab Support Panel.
Generates 12 icons in Aura Silhouette style (soft-glow, black/charcoal silhouettes, dark orange glow).
Uses bold, solid shapes (mảng) and 1/2 splits to avoid coarse thin lines.
Background is transparent (RGBA) for clean presentation on Revit Ribbon.
Downscales to 64x64 and copies to target directories.
"""

import os
import math
from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tabdir import tab_path  # noqa: E402
SUPPORT_PANEL_DIR = tab_path("Support.panel")
PREVIEW_DIR = r"C:\Users\tran_tienthanh\.gemini\antigravity\brain\fc9a3b7f-ab70-4e15-861b-b2d3e68112b6"

# Color constants
GLOW_COLOR = (255, 90, 0, 255)       # Dark Orange neon glow
SILHOUETTE_COLOR = (18, 18, 18, 255) # Soft charcoal black silhouette
HIGHLIGHT_COLOR = (255, 140, 0, 255) # Glowing highlight details (orange)
WHITE_COLOR = (245, 245, 250, 255)   # Glowing white details

# ---------------------------------------------------------------------------
# Shape Drawing Functions (1024 x 1024 canvas)
# ---------------------------------------------------------------------------

def draw_t3lab_assistant(draw, size, color):
    # Solid head block
    draw.rounded_rectangle([250, 320, 774, 750], radius=100, fill=color)
    # Cute solid ears
    draw.rounded_rectangle([280, 200, 380, 350], radius=30, fill=color)
    draw.rounded_rectangle([644, 200, 744, 350], radius=30, fill=color)

def draw_t3lab_assistant_highlights(draw, size):
    # Two large bright white glowing circular eyes (like the cat reference)
    draw.ellipse([367, 485, 457, 575], fill=WHITE_COLOR)
    draw.ellipse([567, 485, 657, 575], fill=WHITE_COLOR)


def draw_mcp_control(draw, size, color):
    # Solid console container
    draw.rounded_rectangle([250, 200, 774, 824], radius=40, fill=color)

def draw_mcp_control_highlights(draw, size):
    # Zebra-stripe / split panel design (bold blocks)
    # Panel 1 (Top)
    draw.rounded_rectangle([300, 260, 724, 400], radius=20, fill=HIGHLIGHT_COLOR)
    # Panel 2 (Middle - dark charcoal cutout)
    draw.rounded_rectangle([300, 440, 724, 580], radius=20, fill=SILHOUETTE_COLOR)
    # Panel 3 (Bottom)
    draw.rounded_rectangle([300, 620, 724, 760], radius=20, fill=HIGHLIGHT_COLOR)
    
    # 3 status dots on the panels
    draw.ellipse([340, 310, 380, 350], fill=WHITE_COLOR)
    draw.ellipse([340, 490, 380, 530], fill=HIGHLIGHT_COLOR)
    draw.ellipse([340, 670, 380, 710], fill=WHITE_COLOR)


def draw_pdf_import(draw, size, color):
    # Document sheet with folded corner
    draw.polygon([(250, 200), (600, 200), (774, 374), (774, 824), (250, 824)], fill=color)

def draw_pdf_import_highlights(draw, size):
    # A very bold solid arrow pointing down
    # Arrow stem
    draw.rounded_rectangle([472, 280, 552, 550], radius=10, fill=HIGHLIGHT_COLOR)
    # Arrow head
    draw.polygon([(380, 520), (644, 520), (512, 690)], fill=HIGHLIGHT_COLOR)


def draw_feedback(draw, size, color):
    # Speech bubble with tail
    draw.rounded_rectangle([220, 250, 804, 670], radius=80, fill=color)
    draw.polygon([(280, 660), (200, 780), (380, 660)], fill=color)

def draw_feedback_highlights(draw, size):
    # Solid bold star inside the bubble
    cx, cy = 512, 460
    points = []
    for i in range(10):
        r = 100 if i % 2 == 0 else 40
        angle = i * math.pi / 5 - math.pi / 2
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        points.append((x, y))
    draw.polygon(points, fill=HIGHLIGHT_COLOR)


def draw_cloud_links(draw, size, color):
    # Solid cloud
    draw.ellipse([200, 380, 500, 680], fill=color)
    draw.ellipse([312, 220, 712, 620], fill=color)
    draw.ellipse([524, 380, 824, 680], fill=color)
    draw.rounded_rectangle([250, 480, 774, 680], radius=50, fill=color)

def draw_cloud_links_highlights(draw, size):
    # Two bold solid interlocking links
    # Link 1
    draw.rounded_rectangle([360, 520, 500, 660], radius=30, fill=HIGHLIGHT_COLOR)
    draw.rounded_rectangle([400, 560, 460, 620], radius=10, fill=SILHOUETTE_COLOR)
    # Link 2
    draw.rounded_rectangle([524, 520, 664, 660], radius=30, fill=HIGHLIGHT_COLOR)
    draw.rounded_rectangle([564, 560, 624, 620], radius=10, fill=SILHOUETTE_COLOR)


def draw_acc_platform(draw, size, color):
    # Skyscraper building block
    draw.rounded_rectangle([420, 180, 604, 650], radius=20, fill=color)
    # Cloud at bottom
    draw.ellipse([200, 500, 500, 800], fill=color)
    draw.ellipse([312, 380, 712, 780], fill=color)
    draw.ellipse([524, 500, 824, 800], fill=color)
    draw.rounded_rectangle([250, 600, 774, 800], radius=50, fill=color)

def draw_acc_platform_highlights(draw, size):
    # Clean 1/2 vertical split of the building
    draw.rounded_rectangle([512, 180, 604, 650], radius=20, fill=HIGHLIGHT_COLOR)
    # Rising sun/platform light (solid circle)
    draw.ellipse([342, 220, 462, 340], fill=HIGHLIGHT_COLOR)


def draw_b360_health(draw, size, color):
    # 3 peak building/factory
    draw.rounded_rectangle([250, 450, 774, 750], radius=20, fill=color)
    draw.polygon([(250, 470), (337, 280), (424, 470)], fill=color)
    draw.polygon([(424, 470), (512, 280), (600, 470)], fill=color)
    draw.polygon([(600, 470), (687, 280), (774, 470)], fill=color)

def draw_b360_health_highlights(draw, size):
    # Bold solid medical cross overlay (representing Health)
    draw.rounded_rectangle([472, 360, 552, 640], radius=10, fill=HIGHLIGHT_COLOR)
    draw.rounded_rectangle([380, 462, 644, 542], radius=10, fill=HIGHLIGHT_COLOR)


def draw_bluebeam_health(draw, size, color):
    # Drafting triangle block
    draw.polygon([(250, 220), (250, 774), (804, 774)], fill=color)
    draw.polygon([(320, 360), (320, 704), (664, 704)], fill=SILHOUETTE_COLOR)

def draw_bluebeam_health_highlights(draw, size):
    # Very bold solid checkmark overlapping
    draw.line([(550, 620), (650, 720), (820, 450)], fill=HIGHLIGHT_COLOR, width=60, joint="round")


def draw_ui_stack(draw, size, color):
    # Solid outer square
    draw.rounded_rectangle([200, 200, 824, 824], radius=60, fill=color)

def draw_ui_stack_highlights(draw, size):
    # Three thick solid layout blocks
    # Header block
    draw.rounded_rectangle([250, 250, 774, 380], radius=20, fill=HIGHLIGHT_COLOR)
    # Sidebar block
    draw.rounded_rectangle([250, 430, 470, 774], radius=20, fill=HIGHLIGHT_COLOR)
    # Content card block
    draw.rounded_rectangle([512, 430, 774, 774], radius=20, fill=HIGHLIGHT_COLOR)


def draw_bg_theme(draw, size, color):
    # Base circle
    draw.ellipse([212, 212, 812, 812], fill=color)

def draw_bg_theme_highlights(draw, size):
    # Clean 1/2 vertical split (Right half circle is glowing orange, left is black)
    draw.pieslice([212, 212, 812, 812], start=-90, end=90, fill=HIGHLIGHT_COLOR)


def draw_ribbon_names(draw, size, color):
    # Main banner and swallowtails
    draw.rounded_rectangle([220, 380, 804, 640], radius=30, fill=color)
    draw.polygon([(220, 420), (100, 350), (160, 512), (100, 670), (220, 600)], fill=color)
    draw.polygon([(804, 420), (924, 350), (864, 512), (924, 670), (804, 600)], fill=color)

def draw_ribbon_names_highlights(draw, size):
    # Bold solid inner badge block
    draw.rounded_rectangle([300, 460, 724, 560], radius=15, fill=HIGHLIGHT_COLOR)


def draw_llms_setting(draw, size, color):
    # Gear body: bold central disc
    draw.ellipse([280, 280, 744, 744], fill=color)
    # 8 chunky axis-aligned teeth around the disc
    cx, cy = 512, 512
    tooth = 130
    offsets = [
        (0, -390), (0, 390), (-390, 0), (390, 0),
        (-276, -276), (276, -276), (-276, 276), (276, 276),
    ]
    for dx, dy in offsets:
        x, y = cx + dx, cy + dy
        draw.rounded_rectangle(
            [x - tooth / 2, y - tooth / 2, x + tooth / 2, y + tooth / 2],
            radius=24, fill=color)

def draw_llms_setting_highlights(draw, size):
    # Inner cutout ring (charcoal) + glowing AI core
    draw.ellipse([400, 400, 624, 624], fill=SILHOUETTE_COLOR)
    draw.ellipse([462, 462, 562, 562], fill=HIGHLIGHT_COLOR)


def draw_tab_manager(draw, size, color):
    # Back Tab (Silhouette)
    draw.rounded_rectangle([220, 280, 620, 680], radius=40, fill=color)

def draw_tab_manager_highlights(draw, size):
    # Front Tab (Glowing orange solid card overlapping)
    draw.rounded_rectangle([404, 380, 804, 780], radius=40, fill=HIGHLIGHT_COLOR)
    # Tab header bar (White color accent block)
    draw.rounded_rectangle([404, 380, 804, 460], radius=20, fill=WHITE_COLOR)

# ---------------------------------------------------------------------------
# Core Rendering Engine
# ---------------------------------------------------------------------------

def render_icon(draw_shape_func, draw_highlights_func, name):
    size = 1024
    
    # 1. Create wide glow (aura)
    glow_im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_im)
    draw_shape_func(glow_draw, size, GLOW_COLOR)
    glow_blurred = glow_im.filter(ImageFilter.GaussianBlur(radius=85))
    
    # 2. Create intense core glow
    glow_core = glow_im.filter(ImageFilter.GaussianBlur(radius=30))
    
    # Combine glows on transparent background
    base_im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    base_im.alpha_composite(glow_blurred)
    base_im.alpha_composite(glow_core)
    
    # 3. Create dark silhouette
    sil_im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sil_draw = ImageDraw.Draw(sil_im)
    draw_shape_func(sil_draw, size, SILHOUETTE_COLOR)
    
    # Apply soft blur to silhouette edges for the soft-focus look
    sil_blurred = sil_im.filter(ImageFilter.GaussianBlur(radius=12))
    base_im.alpha_composite(sil_blurred)
    
    # 4. Add highlights on top
    if draw_highlights_func:
        high_im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        high_draw = ImageDraw.Draw(high_im)
        draw_highlights_func(high_draw, size)
        
        # Soften highlights slightly to merge beautifully
        high_blurred = high_im.filter(ImageFilter.GaussianBlur(radius=3))
        base_im.alpha_composite(high_blurred)
        
    # Save high-res preview in the artifacts folder with a black background
    # (so it displays nicely in markdown preview)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    preview_path = os.path.join(PREVIEW_DIR, f"preview_{name}.png")
    
    preview_bg = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    preview_bg.alpha_composite(base_im)
    preview_bg.convert("RGB").save(preview_path, "PNG")
    
    # Downscale base_im (which has transparent background) to 64x64 using Lanczos filter
    final_im = base_im.resize((64, 64), Image.Resampling.LANCZOS)
    return final_im

# ---------------------------------------------------------------------------
# Icon Mapping and Execution
# ---------------------------------------------------------------------------

ICONS_CONFIG = [
    {
        "name": "t3lab_assistant",
        "shape": draw_t3lab_assistant,
        "highlights": draw_t3lab_assistant_highlights,
        "dest_rel_path": "T3LabAssistant.pushbutton"
    },
    {
        "name": "mcp_control",
        "shape": draw_mcp_control,
        "highlights": draw_mcp_control_highlights,
        "dest_rel_path": "Assistant Tools.stack/MCPControl.pushbutton"
    },
    {
        "name": "pdf_import",
        "shape": draw_pdf_import,
        "highlights": draw_pdf_import_highlights,
        "dest_rel_path": "PDF import.pushbutton"
    },
    {
        "name": "feedback",
        "shape": draw_feedback,
        "highlights": draw_feedback_highlights,
        "dest_rel_path": "Assistant Tools.stack/Feedback.pushbutton"
    },
    {
        "name": "llms_setting",
        "shape": draw_llms_setting,
        "highlights": draw_llms_setting_highlights,
        "dest_rel_path": "Assistant Tools.stack/LLMsSetting.pushbutton"
    },
    {
        "name": "cloud_links",
        "shape": draw_cloud_links,
        "highlights": draw_cloud_links_highlights,
        "dest_rel_path": "CloudLinks.stack"
    },
    {
        "name": "acc_platform",
        "shape": draw_acc_platform,
        "highlights": draw_acc_platform_highlights,
        "dest_rel_path": "CloudLinks.stack/Autodesk Forma.urlbutton"
    },
    {
        "name": "b360_health",
        "shape": draw_b360_health,
        "highlights": draw_b360_health_highlights,
        "dest_rel_path": "CloudLinks.stack/Autodesk Health.urlbutton"
    },
    {
        "name": "bluebeam_health",
        "shape": draw_bluebeam_health,
        "highlights": draw_bluebeam_health_highlights,
        "dest_rel_path": "CloudLinks.stack/Bluebeam Status.urlbutton"
    },
    {
        "name": "ui_stack",
        "shape": draw_ui_stack,
        "highlights": draw_ui_stack_highlights,
        "dest_rel_path": "UI.stack"
    },
    {
        "name": "bg_theme",
        "shape": draw_bg_theme,
        "highlights": draw_bg_theme_highlights,
        "dest_rel_path": "UI.stack/BG Theme.pushbutton"
    },
    {
        "name": "ribbon_names",
        "shape": draw_ribbon_names,
        "highlights": draw_ribbon_names_highlights,
        "dest_rel_path": "UI.stack/Ribbon Names.pushbutton"
    },
    {
        "name": "tab_manager",
        "shape": draw_tab_manager,
        "highlights": draw_tab_manager_highlights,
        "dest_rel_path": "UI.stack/Tab Manager.pushbutton"
    }
]

def main():
    print("Starting procedural icon generation...")
    
    for cfg in ICONS_CONFIG:
        name = cfg["name"]
        print(f"\nProcessing icon: {name}")
        
        # Render the icon image
        icon_img = render_icon(cfg["shape"], cfg["highlights"], name)
        
        # Save path
        dest_folder = os.path.join(SUPPORT_PANEL_DIR, cfg["dest_rel_path"])
        os.makedirs(dest_folder, exist_ok=True)
        
        # 1. Save new icon.png (64x64) as transparent RGBA
        png_path = os.path.join(dest_folder, "icon.png")
        icon_img.save(png_path, "PNG")
        print(f"Saved: {png_path}")
        
        # 2. Deactivate overriding icon.svg if it exists
        svg_path = os.path.join(dest_folder, "icon.svg")
        if os.path.exists(svg_path):
            bak_path = svg_path + ".bak"
            if os.path.exists(bak_path):
                os.remove(bak_path) # Clean up old backup if it exists
            os.rename(svg_path, bak_path)
            print(f"Deactivated: {svg_path} -> {bak_path}")

    print("\nIcon generation and installation complete!")

if __name__ == "__main__":
    main()
