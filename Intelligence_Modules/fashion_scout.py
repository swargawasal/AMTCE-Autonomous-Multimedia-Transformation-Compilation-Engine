"""
FASHION SCOUT — PROXY MODULE (VIRTUAL BRIDGE)
=============================================
This is a redirection shim to the production engine.
Prevents breakage of legacy imports while enforcing 
the use of the Monetization_Metrics v2.1 engine.

Source of Truth:
d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Monetization_Metrics\fashion_scout.py
"""

# Redirect all imports to the production engine
from Monetization_Metrics.fashion_scout import *

# Explicitly export key objects for IDE clarity
__all__ = ['scout', 'FashionScout', 'get_fallback_payload', 'TREND_CONTEXT_PROMPT']
