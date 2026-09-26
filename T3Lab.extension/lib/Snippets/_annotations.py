# -*- coding: utf-8 -*-
"""
Annotations Snippets

Code snippets for working with Revit annotation elements.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Annotations Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
#====================================================================================================
from Autodesk.Revit.DB import *
from pyrevit import forms

# CUSTOM IMPORTS
from Snippets._convert import convert_cm_to_feet
from Snippets._filtered_element_collector import all_legends

#>>>>>>>>>> .NET IMPORTS
import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System")
from System.Collections.Generic import List


# ╔═╗╔╗╔╔╗╔╔═╗╔╦╗╔═╗╔╦╗╦╔═╗╔╗╔╔═╗
# ╠═╣║║║║║║║ ║ ║ ╠═╣ ║ ║║ ║║║║╚═╗
# ╩ ╩╝╚╝╝╚╝╚═╝ ╩ ╩ ╩ ╩ ╩╚═╝╝╚╝╚═╝ ANNOTATIONS
#==================================================
def create_text_note(doc, view, x ,y ,text, text_note_type, bold=False):
    #type:(Document, View, float, float, str, ElementId) -> TextNote
    """Function to create a TextNote.
    :param doc:             Revit Document
    :param view:            View
    :param x:               Position Coordinate X
    :param y:               Position Coordinate Y
    :param text:            TextNote Content
    :param text_note_type:  text_note_type
    :return:                TextNote"""
    text = '-' if not text else text
    # TEXTNOTE
    text_note = TextNote.Create(doc, view.Id, XYZ(x, y, 0),text, text_note_type.Id)

    if bold:
        formatted_text = FormattedText(text)
        formatted_text.SetBoldStatus(True)
        text_note.SetFormattedText(formatted_text)
    return text_note


