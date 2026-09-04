from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .v001_baseline import migrate as migrate_v001
from .v002_auth_and_ownership import migrate as migrate_v002
from .v003_reader_history import migrate as migrate_v003
from .v004_memory import migrate as migrate_v004
from .v005_research_sessions import migrate as migrate_v005
from .v006_document_rag import migrate as migrate_v006
from .v007_embedding_status import migrate as migrate_v007
from .v008_ingest_job_lifecycle import migrate as migrate_v008
from .v009_runtime_tables_and_feedback_isolation import migrate as migrate_v009
from .v010_bilingual_fts_and_memory_index import migrate as migrate_v010
from .v011_memory_retrieval import migrate as migrate_v011


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum_seed: str
    apply: Callable


MIGRATIONS = (
    Migration(1, "phase1_baseline", "2026-07-27:v2:atomic-papers-auth-users", migrate_v001),
    Migration(2, "auth_and_ownership", "2026-07-27:v3:canonical-aux-ownership", migrate_v002),
    Migration(3, "reader_history", "2026-07-27:v2:atomic-reader-conversations", migrate_v003),
    Migration(4, "canonical_memory", "2026-07-27:v2:atomic-memory-draft-commit", migrate_v004),
    Migration(5, "research_sessions", "2026-07-27:v1:user-scoped-multi-paper-chat", migrate_v005),
    Migration(6, "document_rag", "2026-07-28:v1:canonical-pages-blocks-chunks-ingest", migrate_v006),
    Migration(7, "embedding_status", "2026-07-28:v1:version-scoped-dense-projection-status", migrate_v007),
    Migration(8, "ingest_job_lifecycle", "2026-07-28:v1:lease-heartbeat-retry-schedule", migrate_v008),
    Migration(9, "runtime_tables_and_feedback_isolation", "2026-07-28:v1:user-scoped-runtime-tables-no-auto-negative-memory", migrate_v009),
    Migration(10, "bilingual_fts_and_memory_index", "2026-07-28:v1:trigram-cjk-sparse-projection", migrate_v010),
    Migration(11, "memory_retrieval", "2026-07-28:v1:scope-safe-memory-dual-fts", migrate_v011),
)

__all__ = ["MIGRATIONS", "Migration"]
