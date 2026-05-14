# -*- coding: utf-8 -*-
"""
Auto Clicker

Automate repetitive mouse clicking at specified screen coordinates.
Allows picking screen location via a 3-second countdown and customizing
click intervals and total click counts.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__title__   = "Auto Clicker"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==============================================================================
import os
import sys
import clr
import time
import ctypes

clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from System.Windows import WindowState
from System.Windows.Forms import Cursor
from System.Threading import Thread

from pyrevit import revit, forms, script

extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir       = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

logger = script.get_logger()

# ============================================================
# WINDOW CLASS
# ============================================================
class AutoClickWindow(forms.WPFWindow):
    def __init__(self):
        xaml_file_path = os.path.join(extension_dir, 'lib', 'GUI', 'Tools', 'AutoClick.xaml')
        forms.WPFWindow.__init__(self, xaml_file_path)

    # -------- chrome / title bar --------
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()

    # -------- main actions --------
    def _set_status(self, text, error=False):
        self.status_text.Text = text
        from System.Windows.Media import SolidColorBrush, Color
        if error:
            self.status_text.Foreground = SolidColorBrush(Color.FromRgb(231, 76, 60))
        else:
            self.status_text.Foreground = SolidColorBrush(Color.FromRgb(127, 140, 141))

    def pick_location_clicked(self, sender, e):
        self._set_status("Minimizing window for 3 seconds... Move your mouse to target!")
        self.WindowState = WindowState.Minimized
        Thread.Sleep(3000)
        
        pos = Cursor.Position
        self.txt_x.Text = str(pos.X)
        self.txt_y.Text = str(pos.Y)
        
        self.WindowState = WindowState.Normal
        self._set_status("Location captured: X={}, Y={}".format(pos.X, pos.Y))

    def start_clicked(self, sender, e):
        try:
            x = int(self.txt_x.Text)
            y = int(self.txt_y.Text)
            interval = float(self.txt_interval.Text)
            total_clicks = int(self.txt_clicks.Text)
        except ValueError:
            self._set_status("Please enter valid numeric values for coordinates, interval, and clicks.", error=True)
            return

        if total_clicks <= 0:
            self._set_status("Total clicks must be greater than 0.", error=True)
            return

        self._set_status("Starting Auto Click in 2 seconds...")
        self.WindowState = WindowState.Minimized
        Thread.Sleep(2000)

        # MOUSEEVENTF_LEFTDOWN = 0x0002
        # MOUSEEVENTF_LEFTUP = 0x0004

        # Flush existing key states before starting the click loop
        for vk in range(8, 256):
            ctypes.windll.user32.GetAsyncKeyState(vk)

        for i in range(total_clicks):
            # Check if any key is pressed to abort
            abort = False
            for vk in range(8, 256):
                if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                    abort = True
                    break
            if abort:
                self.WindowState = WindowState.Normal
                self._set_status("Auto Click stopped (Key pressed).", error=True)
                return

            # Perform click
            ctypes.windll.user32.SetCursorPos(x, y)
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
            Thread.Sleep(50)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
            
            if i < total_clicks - 1:
                elapsed = 0.0
                while elapsed < interval:
                    for vk in range(8, 256):
                        if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                            self.WindowState = WindowState.Normal
                            self._set_status("Auto Click stopped (Key pressed).", error=True)
                            return
                    Thread.Sleep(50)
                    elapsed += 0.05

        self.WindowState = WindowState.Normal
        self._set_status("Successfully completed {} clicks!".format(total_clicks))


# MAIN SCRIPT
# ==============================================================================
if __name__ == '__main__':
    AutoClickWindow().ShowDialog()
