"""Legacy shim for compiler module."""
from Compiler_Modules.compiler import *
import Compiler_Modules.compiler as _mod

# Ensure all properties are reachable as direct callable references.
# Using getattr + explicit assignment prevents the 'module object is not callable'
# error that occurs when a circular import shadows these names with the module itself.
check_health           = _mod.check_health
_save_sidecar          = _mod._save_sidecar
Path                   = getattr(_mod, "Path", None)

# compile_with_transitions is the most commonly called entry point.
# Resolve it defensively so callers like main.py get the function, not the module.
_compile_with_transitions = getattr(_mod, "compile_with_transitions", None)
if callable(_compile_with_transitions):
    compile_with_transitions = _compile_with_transitions
else:
    # Last-resort: import directly from the module's source to bypass any shadow
    from Compiler_Modules.compiler import compile_with_transitions  # noqa: F811