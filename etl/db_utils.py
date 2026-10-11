"""Small helper to upsert by stable identity. Shared by B-class tables (global
dictionaries, independent of source).

The "supplement only + insert only" rule: an identity already present -> **updated in
place**; a new one -> inserted. Never "read existing, filter out the ones already
there" and append -- that is "skip if it exists", so field improvements on existing
rows (smiles / equation, etc.) can never come in.
"""
import os
import re
import sys

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# schema.sql comes from **this repo**, it does not follow IGEM_DATA_DIR -- that env
# var is for swapping the sandbox's for_* data directory, while the DDL belongs to
# the code, and a second copy must not appear in the sandbox (it would drift).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_SQL = os.path.join(_REPO_ROOT, 'sql', 'schema.sql')

_MISSING_DDL = {}


def schema_ddl(table):
    """Fetch a table's CREATE TABLE statement from sql/schema.sql -- the single source
    of the DDL.

    Several ETL modules used to embed their own DDL copy (`gene_sequence_link` /
    `enzyme_go` / `enzyme_isoform` / `search_index`), duplicating schema.sql. Miss one
    place when the schema changes and the local DDL drifts from schema.sql -- and a
    drifted table structure raises no error, it just makes downstream read empty
    columns.
    """
    if table in _MISSING_DDL:
        raise KeyError(f'sql/schema.sql 里找不到 {table} 的建表语句')
    if not os.path.exists(SCHEMA_SQL):
        raise FileNotFoundError(f'找不到 {SCHEMA_SQL}')
    with open(SCHEMA_SQL, encoding='utf-8') as f:
        sql = f.read()
    m = re.search(rf'CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\(.*?\n\)\s*ENGINE=[^;]*;',
                  sql, re.S)
    if not m:
        # Remember it, so each table does not re-read this 400-line file again.
        _MISSING_DDL[table] = True
        raise KeyError(f'sql/schema.sql 里找不到 {table} 的建表语句')
    return m.group(0)

# MySQL marks VALUES() deprecated in newer versions, but it is still a usable standard
# form on 8.0.46, and it is more compatible than the 8.0.19+ alias syntax (which
# MariaDB does not accept).
def upsert_dataframe(conn, table, df, update_cols, preserve=()):
    """INSERT ... ON DUPLICATE KEY UPDATE, with the conflict key decided by the table's
    PK/UNIQUE.

    update_cols: columns to update on conflict
    preserve:    these columns use COALESCE(VALUES(c), c) -- passing NULL does not wipe
                 the current value. For columns "not carried by this run's data, but
                 whose existing value is still valid" (e.g. compound.inchi_key).

    ⚠️ **The return value is the input row count (`len(df)`), not the rows actually
    written** -- it is not `cursor.rowcount`. Two reasons: (1) MySQL counts an "update"
    as 2 in the rowcount of ON DUPLICATE KEY UPDATE, so using it as a row count doubles
    the number; (2) with INSERT IGNORE the rowcount excludes rows dropped on conflict,
    so `update_cols=[]` (pure insert-only) returns 0 on an **idempotent re-run** -- yet
    every call site prints it as "how many rows this run processed". So the print must
    read "input N rows", not "inserted N rows"; whether anything was actually inserted
    has to come from the change in table row count (on 2026-09-19 this misreported
    "inserted 0 rows" as "inserted 112 rows").
    """
    if df.empty:
        return 0
    cols = list(df.columns)
    placeholders = ', '.join(f':{c}' for c in cols)
    col_list = ', '.join(cols)

    if update_cols:
        sets = ', '.join(
            f'{c} = COALESCE(VALUES({c}), {c})' if c in preserve else f'{c} = VALUES({c})'
            for c in update_cols
        )
        sql = f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {sets}'
    else:
        # No columns to update = pure "insert only"; on conflict, ignore.
        sql = f'INSERT IGNORE INTO {table} ({col_list}) VALUES ({placeholders})'

    # NaN -> None: otherwise pandas writes NaN as a float into VARCHAR columns.
    # astype(object) first avoids numpy scalar types (pymysql errors on numpy.int64).
    params = df.astype(object).where(pd.notna(df), None).to_dict('records')
    conn.execute(text(sql), params)
    return len(params)
