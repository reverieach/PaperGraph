#!/usr/bin/env python3
"""Convenience wrapper for ``python -m app.cli.backfill_ingest``."""

from app.cli.backfill_ingest import main


if __name__ == "__main__":
    raise SystemExit(main())
