#!/usr/bin/env python3
"""Entry point — delegates to archagent.api.server."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from archagent.core.config import load_env_file
load_env_file()
from archagent.api.server import main
if __name__ == '__main__':
    main()
