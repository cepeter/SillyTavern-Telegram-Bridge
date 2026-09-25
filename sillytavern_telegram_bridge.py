#!/usr/bin/env python3
"""Launch the bridge through the same explicit bootstrap as python -m bridge.main."""

from bridge.main import main

if __name__ == "__main__":
    raise SystemExit(main())
