# -*- coding: utf-8 -*-
"""
Find Replace Dialog
GUI dialog for find and replace operations on Revit elements.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Find Replace Dialog"

import os
from pyrevit import forms
from Autodesk.Revit.DB import (Transaction, View, ViewPlan, ViewSection,
                                View3D, ViewSchedule, ViewDrafting)
from Autodesk.Revit.Exceptions import ArgumentException

import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Uri, UriKind
from System.Diagnostics.Process import Start
from System.Windows import WindowState
from System.Windows.Media.Imaging import BitmapImage
import wpf

from GUI.forms import my_WPF

PATH_SCRIPT = os.path.dirname(__file__)


class FindReplace(my_WPF):
    """GUI for [Views: Find and Replace]"""
    run = False

    def __init__(self, title, label="Find and Replace", button_name="Rename"):
        path_xaml_file = os.path.join(PATH_SCRIPT, 'Tools', 'FindReplace.xaml')
        wpf.LoadComponent(self, path_xaml_file)

        self.UI_label.Content       = label
        self.UI_main_button.Content = button_name
        self.main_title.Text        = title

        self.ShowDialog()

