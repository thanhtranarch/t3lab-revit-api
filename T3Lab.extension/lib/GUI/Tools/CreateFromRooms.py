# -*- coding: utf-8 -*-
"""
Create From Rooms

Create elements based on room boundaries and properties.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Create From Rooms"

import os, clr

from pyrevit import forms
from GUI.forms         import my_WPF
from Snippets._convert import convert_internal_units

clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System import Uri, UriKind
from System.Collections.Generic import List
from System.Windows import WindowState
from System.Windows.Media.Imaging import BitmapImage
import wpf

PATH_SCRIPT = os.path.dirname(__file__)


class ListItem:
    def __init__(self, Name='Unnamed', element=None, checked=False):
        self.Name      = Name
        self.IsChecked = checked
        self.element   = element


class CreateFromRooms(my_WPF):
    selected_type = []
    offset        = 0

    def __init__(self, items, title='__title', label="Select Type:",
                 button_name='Create', version='version= 1.0'):
        self.items       = items
        self.title       = title
        self.label       = label
        self.button_name = button_name
        self.version     = version

        path_xaml_file = os.path.join(PATH_SCRIPT, 'CreateFromRooms.xaml')
        wpf.LoadComponent(self, path_xaml_file)

        self.update_UI()
        self.ShowDialog()

