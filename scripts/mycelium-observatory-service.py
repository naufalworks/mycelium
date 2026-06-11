#!/Users/azfar.naufal/.hermes/myceliumd/venv/bin/python3
"""launchd wrapper for mycelium observatory"""
import os
import sys

os.chdir("/Users/azfar.naufal/Documents/mycelium")
sys.path.insert(0, "/Users/azfar.naufal/Documents/mycelium")

import uvicorn

uvicorn.run("web.backend.app:app", host="127.0.0.1", port=8421)
