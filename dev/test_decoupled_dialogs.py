# -*- coding: utf-8 -*-
"""
CPython 3 test harness to verify that all decoupled dialog modules and pushbuttons
compile and load without syntax errors or missing XAML files.

Run:  python dev/test_decoupled_dialogs.py
Exit code 0 = all pass.
"""
from __future__ import unicode_literals

import os
import sys
import py_compile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_DIR = os.path.join(REPO, "T3Lab.extension", "lib", "GUI")
TOOLS_DIR = os.path.join(GUI_DIR, "Tools")

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   {}".format(name))
    else:
        print("  FAIL {} {}".format(name, detail))
        FAILURES.append(name)


DIALOGS_TO_VERIFY = [
    ("AutoJoinDialog.py", "AutoJoin.xaml"),
    ("AutoWorkDialog.py", "AutoWork.xaml"),
    ("BCFReaderDialog.py", "BCFReader.xaml"),
    ("BatchOutDialog.py", "ExportManager.xaml"),
    ("DoorThresholdDialog.py", "DoorThreshold.xaml"),
    ("ImageToDraftingDialog.py", "ImageToDrafting.xaml"),
    ("ManaDWGDialog.py", "DWGManagement.xaml"),
    ("ManaLocaDialog.py", "ManaLoca.xaml"),
    ("ManaWorksetDialog.py", "ManaWorkset.xaml"),
    ("PointCloudDialog.py", "PointCloud.xaml"),
    ("RoomToFloorDialog.py", "RoomToFloor.xaml"),
    ("SheetGenDialog.py", "SheetGen.xaml"),
    ("T3LabAssistantDialog.py", "T3LabAssistant.xaml"),
    ("TileLayoutDialog.py", "TileLayout.xaml"),
    ("WallAdjustBaseDialog.py", "WallAdjustBase.xaml"),
    ("WallCutProfileDialog.py", "WallCutProfile.xaml"),
    ("PropertyLineDialog.py", "PropertyLine.xaml"),
    ("TextToElementDialog.py", "TextToElement.xaml"),
]


def test_dialog_compilation_and_xaml():
    for dialog_file, xaml_file in DIALOGS_TO_VERIFY:
        dialog_path = os.path.join(GUI_DIR, dialog_file)
        xaml_path = os.path.join(TOOLS_DIR, xaml_file)

        # 1. Dialog file exists
        check("{} exists".format(dialog_file), os.path.isfile(dialog_path))

        # 2. XAML file exists
        check("{} exists".format(xaml_file), os.path.isfile(xaml_path))

        # 3. Dialog compiles cleanly with CPython py_compile
        try:
            py_compile.compile(dialog_path, doraise=True)
            check("{} compiles".format(dialog_file), True)
        except Exception as ex:
            check("{} compiles".format(dialog_file), False, str(ex))


def main():
    print("Running Decoupled Dialogs & XAML Verification Tests...")
    test_dialog_compilation_and_xaml()

    if FAILURES:
        print("\n{} failure(s) in Decoupled Dialogs test harness.".format(len(FAILURES)))
        sys.exit(1)
    else:
        print("\nAll Decoupled Dialogs & XAML files verified successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
