#!/usr/bin/env python3
"""Convenience wrapper for ``python -m app.cli.preflight``."""

from app.cli.preflight import main


if __name__ == "__main__":
    raise SystemExit(main())
