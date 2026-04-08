#!/usr/bin/env python3
"""Remove duplicate page-view analytics rows caused by repeated page-exit sends."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def default_db_path() -> Path:
    """Match the application's default SQLite location."""
    data_dir = (
        os.environ.get("GUESTDESK_DATA_DIR")
        or os.environ.get("GUESTD_DATA_DIR")
        or "/var/lib/guestdesk"
    )
    return Path(data_dir) / "guestdesk.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find and optionally delete duplicate analytics page-view rows. "
            "Dry-run by default; pass --apply to delete."
        )
    )
    parser.add_argument(
        "--db",
        default=str(default_db_path()),
        help="Path to the GuestDesk SQLite database.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the duplicate rows instead of only reporting them.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print duplicate groups that will be cleaned.",
    )
    return parser.parse_args()


def ensure_analytics_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_events'"
    ).fetchone()
    return bool(row)


def duplicate_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return duplicate page-view groups, keeping the most complete row."""
    sql = """
        WITH ranked AS (
            SELECT
                id,
                session_id,
                COALESCE(path, '') AS path,
                started_at,
                ended_at,
                duration_ms,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(session_id, ''),
                        COALESCE(path, ''),
                        COALESCE(action, ''),
                        COALESCE(category, ''),
                        COALESCE(label, ''),
                        started_at
                    ORDER BY
                        COALESCE(ended_at, started_at) DESC,
                        COALESCE(duration_ms, 0) DESC,
                        id DESC
                ) AS rownum,
                COUNT(*) OVER (
                    PARTITION BY
                        COALESCE(session_id, ''),
                        COALESCE(path, ''),
                        COALESCE(action, ''),
                        COALESCE(category, ''),
                        COALESCE(label, ''),
                        started_at
                ) AS copies
            FROM analytics_events
            WHERE COALESCE(category, '') = 'page'
              AND COALESCE(action, '') = 'view'
              AND started_at IS NOT NULL
              AND COALESCE(session_id, '') <> ''
        )
        SELECT
            session_id,
            path,
            started_at,
            copies,
            GROUP_CONCAT(id) AS all_ids,
            GROUP_CONCAT(CASE WHEN rownum > 1 THEN id END) AS duplicate_ids
        FROM ranked
        WHERE copies > 1
        GROUP BY session_id, path, started_at, copies
        ORDER BY started_at, session_id, path
    """
    return conn.execute(sql).fetchall()


def duplicate_ids(conn: sqlite3.Connection) -> list[int]:
    """Return row ids to delete, leaving one survivor per duplicate group."""
    sql = """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(session_id, ''),
                        COALESCE(path, ''),
                        COALESCE(action, ''),
                        COALESCE(category, ''),
                        COALESCE(label, ''),
                        started_at
                    ORDER BY
                        COALESCE(ended_at, started_at) DESC,
                        COALESCE(duration_ms, 0) DESC,
                        id DESC
                ) AS rownum
            FROM analytics_events
            WHERE COALESCE(category, '') = 'page'
              AND COALESCE(action, '') = 'view'
              AND started_at IS NOT NULL
              AND COALESCE(session_id, '') <> ''
        )
        SELECT id
        FROM ranked
        WHERE rownum > 1
        ORDER BY id
    """
    return [row[0] for row in conn.execute(sql).fetchall()]


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        if not ensure_analytics_table(conn):
            print(f"No analytics_events table found in {db_path}")
            return 0

        groups = duplicate_groups(conn)
        ids = duplicate_ids(conn)

        print(f"Database: {db_path}")
        print(f"Duplicate page-view groups: {len(groups)}")
        print(f"Duplicate rows to delete: {len(ids)}")

        if args.verbose and groups:
            for group in groups:
                print(
                    f"session={group['session_id']} path={group['path']} "
                    f"started_at={group['started_at']} copies={group['copies']} "
                    f"duplicate_ids={group['duplicate_ids'] or ''}"
                )

        if not args.apply:
            print("Dry run only. Re-run with --apply to delete duplicate rows.")
            return 0

        if not ids:
            print("No duplicate rows found. Nothing to delete.")
            return 0

        try:
            with conn:
                placeholders = ", ".join("?" for _ in ids)
                conn.execute(
                    f"DELETE FROM analytics_events WHERE id IN ({placeholders})",
                    ids,
                )
        except sqlite3.OperationalError as exc:
            print(f"Could not delete rows: {exc}", file=sys.stderr)
            print(
                "The database appears to be read-only for this user. "
                "Re-run with a user that can write to the GuestDesk DB.",
                file=sys.stderr,
            )
            return 1

        print(f"Deleted {len(ids)} duplicate analytics row(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
