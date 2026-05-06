# -*- coding: utf-8 -*-
import os
import tempfile
import time
import subprocess
from pyrevit import revit, DB, forms, script
import clr

clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Windows import Clipboard
from System.Windows.Media.Imaging import BitmapImage, BmpBitmapEncoder, BitmapFrame
from System.IO import FileStream, FileMode
from System import Uri
import Microsoft.Win32

doc = revit.doc

def parse_dxf_polylines(filepath):
    polylines = []
    try:
        with open(filepath, 'r') as f:
            lines = [line.strip() for line in f.readlines()]
            
        current_poly = []
        i = 0
        while i < len(lines):
            if lines[i] == "POLYLINE" or lines[i] == "LWPOLYLINE":
                current_poly = []
                polylines.append(current_poly)
            elif lines[i] == "VERTEX":
                x, y = None, None
                j = i + 1
                while j < len(lines) and lines[j] not in ["VERTEX", "SEQEND", "POLYLINE", "LWPOLYLINE", "EOF"]:
                    if lines[j] == "10":
                        try:
                            x = float(lines[j+1])
                        except: pass
                    elif lines[j] == "20":
                        try:
                            y = float(lines[j+1])
                        except: pass
                    j += 1
                if x is not None and y is not None:
                    current_poly.append((x, y))
            i += 1
    except Exception as e:
        print("Error parsing DXF: " + str(e))
    return polylines

class ImageToDraftingWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.image_path = None
        self.temp_files = []
        self.potrace_path = os.path.join(os.path.dirname(__file__), "potrace.exe")
        
    def Window_KeyDown(self, sender, args):
        if str(args.Key) == "V" and str(args.KeyboardDevice.Modifiers) == "Control":
            self.load_from_clipboard()

    def Border_Drop(self, sender, args):
        if args.Data.GetDataPresent("FileDrop"):
            files = args.Data.GetData("FileDrop")
            if files and len(files) > 0:
                self.load_image_from_file(files[0])

    def Browse_Click(self, sender, args):
        dialog = Microsoft.Win32.OpenFileDialog()
        dialog.Filter = "Image Files|*.jpg;*.jpeg;*.png;*.bmp|All Files|*.*"
        if dialog.ShowDialog():
            self.load_image_from_file(dialog.FileName)

    def Clear_Click(self, sender, args):
        self.ImagePreview.Source = None
        self.image_path = None

    def load_image_from_file(self, filepath):
        if os.path.exists(filepath):
            # Load preview
            bmp = BitmapImage()
            bmp.BeginInit()
            bmp.UriSource = Uri(filepath)
            bmp.EndInit()
            self.ImagePreview.Source = bmp
            
            # Save as BMP for potrace
            temp_dir = tempfile.gettempdir()
            temp_file = os.path.join(temp_dir, "drafting_image_{}.bmp".format(int(time.time())))
            self.temp_files.append(temp_file)
            
            encoder = BmpBitmapEncoder()
            encoder.Frames.Add(BitmapFrame.Create(bmp))
            stream = FileStream(temp_file, FileMode.Create)
            encoder.Save(stream)
            stream.Close()
            
            self.image_path = temp_file

    def load_from_clipboard(self):
        if Clipboard.ContainsImage():
            imgSource = Clipboard.GetImage()
            if imgSource:
                self.ImagePreview.Source = imgSource
                # Save to temp file as BMP
                temp_dir = tempfile.gettempdir()
                temp_file = os.path.join(temp_dir, "drafting_image_{}.bmp".format(int(time.time())))
                self.temp_files.append(temp_file)
                
                encoder = BmpBitmapEncoder()
                encoder.Frames.Add(BitmapFrame.Create(imgSource))
                stream = FileStream(temp_file, FileMode.Create)
                encoder.Save(stream)
                stream.Close()
                
                self.image_path = temp_file

    def Create_Click(self, sender, args):
        view_name = self.ViewNameInput.Text
        if not view_name:
            forms.alert("Please enter a name for the Drafting View.")
            return
        if not self.image_path:
            forms.alert("Please select or paste an image.")
            return
            
        if not os.path.exists(self.potrace_path):
            forms.alert("potrace.exe not found in the tool folder! Cannot vectorize.")
            return

        self.Close()
        self.create_drafting_view_with_potrace(view_name, self.image_path)

    def create_drafting_view_with_potrace(self, view_name, img_path):
        # 1. Run Potrace
        dxf_path = img_path + ".dxf"
        self.temp_files.append(dxf_path)
        
        forms.alert("Running local vectorization engine. Please wait...", exitscript=False)
        try:
            # -b dxf means output DXF format
            # -u 1 means unit scale
            cmd = [self.potrace_path, "-b", "dxf", img_path, "-o", dxf_path]
            subprocess.call(cmd, shell=False)
        except Exception as e:
            forms.alert("Error running potrace: " + str(e))
            return
            
        if not os.path.exists(dxf_path):
            forms.alert("Failed to generate DXF from image.")
            return

        # 2. Parse DXF Polylines
        polylines = parse_dxf_polylines(dxf_path)
        if not polylines:
            forms.alert("No line paths were detected in the image. Ensure the image has clear dark lines on a light background.")
            return

        # 3. Create Drafting View
        view_family_type = None
        for vft in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
            if vft.ViewFamily == DB.ViewFamily.Drafting:
                view_family_type = vft
                break
                
        if not view_family_type:
            forms.alert("No drafting view type found in the project.")
            return

        existing_views = [v.Name for v in DB.FilteredElementCollector(doc).OfClass(DB.View)]
        final_view_name = view_name
        if final_view_name in existing_views:
            final_view_name = final_view_name + " " + str(int(time.time()))
            
        # Optional: scale the geometry down a bit if it's too large (potrace uses pixel coordinates)
        # e.g., 100 pixels = 1 inch
        scale_factor = 0.01 
            
        drafting_view = None
        lines_created = 0
        with revit.Transaction("Vectorize Image to Drafting View"):
            try:
                drafting_view = DB.ViewDrafting.Create(doc, view_family_type.Id)
                drafting_view.Name = final_view_name
                
                for poly in polylines:
                    if len(poly) < 2:
                        continue
                    # Potrace Y is inverted, we need to flip it
                    for i in range(len(poly) - 1):
                        p1 = poly[i]
                        p2 = poly[i+1]
                        
                        start_pt = DB.XYZ(p1[0] * scale_factor, -p1[1] * scale_factor, 0)
                        end_pt = DB.XYZ(p2[0] * scale_factor, -p2[1] * scale_factor, 0)
                        
                        if start_pt.DistanceTo(end_pt) > doc.Application.ShortCurveTolerance:
                            geom_line = DB.Line.CreateBound(start_pt, end_pt)
                            doc.Create.NewDetailCurve(drafting_view, geom_line)
                            lines_created += 1
                
            except Exception as e:
                forms.alert("Error creating Revit Elements:\n" + str(e))
                return
                
        if drafting_view:
            uidoc = revit.uidoc
            uidoc.ActiveView = drafting_view
            print("Successfully created {} detail lines.".format(lines_created))

    def cleanup(self):
        for t in self.temp_files:
            try:
                if os.path.exists(t):
                    os.remove(t)
            except:
                pass

def main():
    if not doc:
        forms.alert("No active document.")
        return
        
    xaml_file = script.get_bundle_file('ui.xaml')
    window = ImageToDraftingWindow(xaml_file)
    window.ShowDialog()
    window.cleanup()

if __name__ == '__main__':
    main()
