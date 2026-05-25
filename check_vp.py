import sys, os, inspect
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")
from Compiler_Modules.video_pipeline import render_pipeline
import re

# Read the source to understand what timeline_instructions expects
src = open(r"D:\AMTCE-short-form-editor-only\Compiler_Modules\video_pipeline.py").read()
# Find the function body
start = src.find("def render_pipeline")
end = src.find("\ndef ", start+20)
print(src[start:start+1500])
