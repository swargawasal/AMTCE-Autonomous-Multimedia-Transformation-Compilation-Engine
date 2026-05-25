"""Fix Dimension 5 directly by line number"""
from pathlib import Path

TARGET = Path(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Monetization_Metrics\fashion_scout.py")
lines = TARGET.read_text(encoding="utf-8").split("\n")

# Find the line with DIMENSION 5
for i, line in enumerate(lines):
    if "DIMENSION 5" in line and "COLOUR" in line:
        print(f"Found at line {i+1}: {repr(line)}")
        # Replace lines i through i+3 (the 3 scoring lines + blank)
        lines[i]   = "   DIMENSION 5 \u2014 COLOUR SIGNAL on FOREGROUND SUBJECT only (max 5 pts):"
        lines[i+1] = "  CRITICAL: Only score the PRIMARY SUBJECT\u2019s garment colour here."
        lines[i+2] = "  Background people\u2019s bright clothing (sarees, suits in background) = 0 pts. Ignored."
        # Insert new lines after
        new_lines = (
            ["     5 pts \u2192 PRIMARY SUBJECT\u2019s garment has bold/saturated colour",
             "     3 pts \u2192 PRIMARY SUBJECT\u2019s garment has moderate colour presence",
             "     1 pt  \u2192 PRIMARY SUBJECT\u2019s garment is low saturation / neutral / muted"]
        )
        lines = lines[:i+3] + new_lines + lines[i+3:]
        break
else:
    print("ERROR: DIMENSION 5 not found!")
    exit(1)

TARGET.write_text("\n".join(lines), encoding="utf-8")
print("Done. Verification:")
content = TARGET.read_text(encoding="utf-8")
print("  FOREGROUND SUBJECT ONLY in Dim5:", "PRIMARY SUBJECT" in content and "DIMENSION 5" in content)
print("  Syntax check...")
import py_compile, tempfile, shutil, os
tmp = tempfile.mktemp(suffix=".py")
shutil.copy2(TARGET, tmp)
try:
    py_compile.compile(tmp, doraise=True)
    print("  SYNTAX OK")
except py_compile.PyCompileError as e:
    print(f"  SYNTAX ERROR: {e}")
finally:
    os.unlink(tmp)
