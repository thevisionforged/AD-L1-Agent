"""Start the AD Identity L1 Virtual Agent web app (used by run.ps1).

Built by Samarth Pandey (hack-grp-22).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from l1agent.config import get_settings  # noqa: E402

if __name__ == "__main__":
    s = get_settings()
    mode = f"ONLINE (model {s.model})" if s.online else "OFFLINE rule-based mode (no OPENAI_API_KEY set)"
    print(f"\n  AD Identity L1 Virtual Agent - {mode}\n  Open http://{s.host}:{s.port} in your browser\n", flush=True)
    uvicorn.run("l1agent.api:app", host=s.host, port=s.port, log_level=os.getenv("LOG_LEVEL", "warning"))
