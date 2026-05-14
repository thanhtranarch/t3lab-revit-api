import re

file_path = r"d:\01. T3Lab\02 Revit Tools\t3lab-revit-api\T3Lab.extension\T3Lab.tab\Project.panel\FamilyWork.stack\CAD To Family.pushbutton\script.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

class_code = """
class WarningSwallower(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        fail_list = failuresAccessor.GetFailureMessages()
        if fail_list.Count == 0:
            return FailureProcessingResult.Continue
        for failure in fail_list:
            if failure.GetSeverity() == FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue

def start_transaction(t):
    options = t.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(WarningSwallower())
    t.SetFailureHandlingOptions(options)
    return t.Start()
"""

# Import IFailuresPreprocessor, FailureProcessingResult, FailureSeverity
content = content.replace(
    "PlanarFace, Solid,",
    "PlanarFace, Solid, IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,"
)

# Insert the class code after the imports
content = content.replace(
    "from Utils.DWGFamilyHelpers import get_xy_bounds, _project_curve_to_z as _dwg_project_curve",
    "from Utils.DWGFamilyHelpers import get_xy_bounds, _project_curve_to_z as _dwg_project_curve\n" + class_code
)

# Replace <var>.Start() with start_transaction(<var>)
content = re.sub(r'^(\s+)([a-zA-Z0-9_]+)\.Start\(\)', r'\1start_transaction(\2)', content, flags=re.MULTILINE)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied.")
