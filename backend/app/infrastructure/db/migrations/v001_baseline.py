from __future__ import annotations

import sqlite3

from .helpers import (
    columns,
    foreign_keys,
    legacy_owner_id,
    now_ts,
    quote_identifier,
    rename_to_backup,
    select_expr,
    table_exists,
)


PAPERS_SQL = """
CREATE TABLE papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    title TEXT NOT NULL,
    abstract TEXT,
    doi TEXT,
    pmid TEXT,
    arxiv_id TEXT,
    pmc_id TEXT,
    journal TEXT,
    venue_type TEXT DEFAULT '',
    year INTEGER,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    publisher TEXT,
    pdf_url TEXT,
    source_url TEXT,
    local_pdf_path TEXT,
    keywords TEXT,
    mesh_terms TEXT,
    "references" TEXT,
    citations INTEGER DEFAULT 0,
    source TEXT DEFAULT 'unknown',
    notes TEXT,
    tags TEXT,
    category TEXT,
    rating INTEGER,
    read_status TEXT DEFAULT 'unread',
    importance TEXT DEFAULT 'normal',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, doi),
    UNIQUE(user_id, pmid),
    UNIQUE(user_id, arxiv_id),
    UNIQUE(user_id, pmc_id)
)
"""


def _create_auth_users(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'disabled')),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_users_username ON auth_users(username)"
    )


def _papers_are_canonical(conn: sqlite3.Connection) -> bool:
    cols = columns(conn, "papers")
    if not {"id", "user_id", "title", "created_at", "updated_at"}.issubset(cols):
        return False
    if not int(cols["user_id"]["notnull"]):
        return False
    return any(
        str(row["table"]) == "auth_users" and str(row["from"]) == "user_id"
        for row in foreign_keys(conn, "papers")
    )


def _drop_papers_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS papers_ai")
    conn.execute("DROP TRIGGER IF EXISTS papers_ad")
    conn.execute("DROP TRIGGER IF EXISTS papers_au")
    if table_exists(conn, "papers_fts"):
        conn.execute("DROP TABLE papers_fts")


def _create_authors(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            affiliation TEXT,
            email TEXT,
            orcid TEXT UNIQUE
        )
        """
    )


def _create_paper_authors(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE paper_authors (
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
            author_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (paper_id, author_id)
        )
        """
    )


def _rebuild_papers(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "papers"):
        conn.execute(PAPERS_SQL)
        _create_authors(conn)
        _create_paper_authors(conn)
        return

    old_cols = columns(conn, "papers")
    old_names = set(old_cols)
    row_count = int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
    owner_id = legacy_owner_id(conn) if row_count else None

    _drop_papers_fts(conn)
    paper_authors_backup = rename_to_backup(conn, "paper_authors")
    papers_backup = rename_to_backup(conn, "papers")
    assert papers_backup is not None

    conn.execute(PAPERS_SQL)
    target_columns = [
        "id",
        "user_id",
        "title",
        "abstract",
        "doi",
        "pmid",
        "arxiv_id",
        "pmc_id",
        "journal",
        "venue_type",
        "year",
        "volume",
        "issue",
        "pages",
        "publisher",
        "pdf_url",
        "source_url",
        "local_pdf_path",
        "keywords",
        "mesh_terms",
        "references",
        "citations",
        "source",
        "notes",
        "tags",
        "category",
        "rating",
        "read_status",
        "importance",
        "created_at",
        "updated_at",
    ]
    now = now_ts()
    expressions: list[str] = []
    for name in target_columns:
        if name == "user_id":
            expressions.append(str(owner_id or 0))
        elif name == "title":
            expressions.append(f"COALESCE({select_expr(old_names, name)}, '')")
        elif name in {"created_at", "updated_at"}:
            raw = select_expr(old_names, name, str(now))
            expressions.append(
                f"COALESCE(CASE WHEN typeof({raw}) IN ('integer','real') "
                f"THEN CAST({raw} AS INTEGER) ELSE CAST(strftime('%s', {raw}) AS INTEGER) END, {now})"
            )
        elif name == "venue_type":
            expressions.append(select_expr(old_names, name, "''"))
        elif name == "citations":
            expressions.append(select_expr(old_names, name, "0"))
        elif name == "source":
            expressions.append(select_expr(old_names, name, "'unknown'"))
        elif name == "read_status":
            expressions.append(select_expr(old_names, name, "'unread'"))
        elif name == "importance":
            expressions.append(select_expr(old_names, name, "'normal'"))
        else:
            expressions.append(select_expr(old_names, name))

    if row_count:
        quoted_targets = ", ".join(quote_identifier(x) for x in target_columns)
        conn.execute(
            f"""
            INSERT INTO papers ({quoted_targets})
            SELECT {", ".join(expressions)}
            FROM {quote_identifier(papers_backup)}
            """
        )

    _create_authors(conn)
    _create_paper_authors(conn)
    if paper_authors_backup:
        pa_cols = columns(conn, paper_authors_backup)
        if {"paper_id", "author_id"}.issubset(pa_cols):
            order_expr = (
                quote_identifier("author_order")
                if "author_order" in pa_cols
                else "0"
            )
            conn.execute(
                f"""
                INSERT OR IGNORE INTO paper_authors(paper_id, author_id, author_order)
                SELECT paper_id, author_id, {order_expr}
                FROM {quote_identifier(paper_authors_backup)}
                """
            )


def _ensure_indexes_and_fts(conn: sqlite3.Connection) -> None:
    for statement in (
        """
        CREATE INDEX IF NOT EXISTS idx_papers_user_created
            ON papers(user_id, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_papers_user_category
            ON papers(user_id, category)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_papers_user_year
            ON papers(user_id, year)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_papers_user_read_status
            ON papers(user_id, read_status)
        """,
    ):
        conn.execute(statement)
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
                title, abstract, content='papers', content_rowid='id'
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
                INSERT INTO papers_fts(rowid, title, abstract)
                VALUES (new.id, new.title, new.abstract);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
                INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
                VALUES ('delete', old.id, old.title, old.abstract);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
                INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
                VALUES ('delete', old.id, old.title, old.abstract);
                INSERT INTO papers_fts(rowid, title, abstract)
                VALUES (new.id, new.title, new.abstract);
            END
            """
        )
        conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        # FTS5 is an optional SQLite build feature. Paper CRUD remains available.
        _drop_papers_fts(conn)


def migrate(conn: sqlite3.Connection) -> None:
    _create_auth_users(conn)
    _create_authors(conn)
    if not _papers_are_canonical(conn):
        _rebuild_papers(conn)
    elif not table_exists(conn, "paper_authors"):
        _create_paper_authors(conn)
    _ensure_indexes_and_fts(conn)
