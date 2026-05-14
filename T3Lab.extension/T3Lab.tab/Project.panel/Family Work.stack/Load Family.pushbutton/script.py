# -*- coding: utf-8 -*-
"""
Load Family

Load Revit families from local disk or cloud library.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Load Family"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==============================================================================
import os
import sys
import traceback

from pyrevit import revit, script

extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

from GUI.FamilyLoaderDialog import show_family_loader

# DEFINE VARIABLES
# ==============================================================================
logger        = script.get_logger()
output        = script.get_output()
REVIT_VERSION = int(revit.doc.Application.VersionNumber)

# MAIN SCRIPT
# ==============================================================================
if __name__ == '__main__':
    try:
        loaded_families = show_family_loader()

        if loaded_families:
            logger.info("Successfully loaded {} families".format(len(loaded_families)))
        else:
            logger.info("No families were loaded")

    except Exception as ex:
        logger.error("Error in Load Family tool: {}".format(ex))
        logger.error(traceback.format_exc())
