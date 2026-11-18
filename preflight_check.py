"""
preflight_check.py: Autonomous Pre-Flight Readiness Auditor

CLI alias that executes the unified full-spectrum diagnostic suite in doctor.py.
Usage:
    python preflight_check.py
    python doctor.py
"""

import asyncio
import sys
from doctor import run_diagnostics, get_preflight_status_dict

if __name__ == "__main__":
    passed = asyncio.run(run_diagnostics())
    sys.exit(0 if passed else 1)
