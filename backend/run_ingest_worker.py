#!/usr/bin/env python3
"""Convenience wrapper for ``python -m app.workers.ingest_worker``."""

from app.workers.ingest_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
