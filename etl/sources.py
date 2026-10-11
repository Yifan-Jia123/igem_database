"""Source-segmented read entry point -- the only way the ETL reads for_* segmented
files.

## Background

`update_tool` writes 11 tables **source-segmented** to disk
(`for_*/<name>.<source>.tsv`), while 3 other tables aggregated across entries
(pairs / compounds / all_nodes) stay suffix-free. So every ETL step reads the union of
all segments.

If each module wrote its own path join/glob, missing one place when a source is added
would make that place **silently read only one source's data** -- the same class of
drift `source_registry.py` guards against on the source side, just in the opposite
direction.

## Single source of the source list

`SOURCES` is imported directly from `../update_tool/source_registry.py`; it is **not
copied here**. The ETL and the `for_*` it reads are one set of artifacts deployed
together, with no scenario of distributing `etl/` separately, so **failing hard** when
the registry cannot be reached is safer than quietly using a local copy (the copy is
exactly where drift comes from).

## Missing file = hard error, not skip

A missing segment file **must raise**. Silently skipping means "this update quietly
lost one source's data", and the tell of such a defect is precisely that "it runs
fine". The danger is asymmetric: reading one extra source just wastes time, reading one
fewer silently loses data.
"""

import os
import re
import sys

import pandas as pd

_ETL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ETL_DIR)
from config import DATA_DIR  # noqa: E402

# The registry always comes from update_tool/ in the real repo -- the sandbox
# only swaps where for_* lives (IGEM_DATA_DIR), not "which sources exist",
# otherwise the sandbox would define its own second set of sources.
_REGISTRY_DIR = os.path.join(os.path.dirname(_ETL_DIR), 'update_tool')


def _load_sources():
    """Load the registered sources from update_tool/source_registry.py (the only
    source)."""
    if _REGISTRY_DIR not in sys.path:
        sys.path.insert(0, _REGISTRY_DIR)
    try:
        from source_registry import SOURCES  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - deployment error, not a data error
        raise RuntimeError(
            f'取不到来源登记表 {_REGISTRY_DIR}/source_registry.py。\n'
            'ETL 需要它来确定要读哪些分段文件。不在那里复制一份来源列表 ——'
            '两份列表会漂移, 而漂移的方向正是「悄悄少读一个来源」。'
        ) from exc
    return tuple(SOURCES)


SOURCES = _load_sources()

# The registry docstring says "order is priority" -- turn that into an **executable**
# mapping instead of leaving "first read wins" scattered across the implicit ordering
# of various concats. When one accession appears in several segments (two download
# windows straddling an upgrade/downgrade), this index is the tie-breaker.
SOURCE_PRIORITY = {s: i for i, s in enumerate(SOURCES)}


def segmented_path(rel_path, source):
    """for_x/y.tsv + source -> absolute path for_x/y.<source>.tsv"""
    base, ext = os.path.splitext(rel_path)
    return os.path.join(DATA_DIR, f'{base}.{source}{ext}')


def segmented_paths(rel_path, only=None):
    """[(source, path), ...]; raises FileNotFoundError listing which are missing.

    With only=<source> it returns just that source (for etl_run.py --source=<s> to
    reload a single source).
    """
    wanted = [only] if only else list(SOURCES)
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        raise ValueError(f'未登记的来源 {unknown}; 已登记: {list(SOURCES)}')

    pairs = [(s, segmented_path(rel_path, s)) for s in wanted]
    missing = [(s, p) for s, p in pairs if not os.path.exists(p)]
    if missing:
        # Cannot skip -- reading one fewer source is silent data loss. See the module
        # docstring.
        lines = '\n'.join(f'  缺 {s}: {p}' for s, p in missing)
        raise FileNotFoundError(
            f'分段文件不齐, 拒绝继续: {rel_path}\n{lines}\n'
            f'  已登记来源: {list(SOURCES)}\n'
            f'  先跑 update_tool: python run_all.py --source=<s> 然后 update_database.py'
        )
    return pairs


def read_segmented(rel_path, only=None, **kwargs):
    """Read all segments and concat. Every row carries a `Source` column (written by
    the source side); this also validates it.

    Why validate: a file-name suffix that disagrees with the actual `Source` value
    means the file was deployed under the wrong suffix (or the source side wrote it
    wrong), which makes source-segmented replacement **delete the wrong rows / keep
    the wrong rows** -- silent and destructive.
    """
    frames = []
    for source, path in segmented_paths(rel_path, only=only):
        df = pd.read_csv(path, sep='\t', **kwargs)
        if 'Source' in df.columns:
            found = {str(v).strip() for v in df['Source'].dropna().unique()}
            found.discard('')
            wrong = found - {source}
            if wrong:
                raise ValueError(
                    f'{path}\n  文件名后缀是 {source!r}, 但 Source 列里出现了 {sorted(wrong)}。\n'
                    '  文件被部署到了错误的后缀下 —— 按来源替换会删错行, 拒绝继续。'
                )
        # Normalise: a missing column or an empty cell is filled with the source from
        # the file name. Downstream always trusts the `Source` column (no longer
        # guesses from the file name), so it must not be empty -- an empty value leaves
        # a row with no source, and source-segmented replacement then misses or keeps
        # it.
        df['Source'] = source
        frames.append(df)

    if not frames:
        raise RuntimeError(f'没有读到任何分段: {rel_path}')
    if len(frames) == 1:
        return frames[0]
    return pd.concat(frames, ignore_index=True)


def columns_of(rel_path, only=None):
    """For intersection-style checks of segment headers: each source may have a
    different column width (SP master 141 columns / TrEMBL 473 columns).

    Returns {source: [column names]}, reading only the header (nrows=0), so it is
    cheap.
    """
    return {s: list(pd.read_csv(p, sep='\t', nrows=0).columns)
            for s, p in segmented_paths(rel_path, only=only)}


def safe_usecols(rel_path, wanted, only=None):
    """Return wanted only if it exists in every segment, otherwise None.

    Passing usecols directly errors on a source that lacks a column -- and column width
    is exactly where sources differ, so this must check before deciding.
    """
    cols = columns_of(rel_path, only=only)
    for source, names in cols.items():
        missing = [c for c in wanted if c not in names]
        if missing:
            print(f'  [usecols] {source} 段缺 {missing}, 本步退回全列读取')
            return None
    return list(wanted)


def read_merged(rel_path, **kwargs):
    """The suffix-free merged tables (pairs / compounds / all_nodes). They are
    independent of source."""
    path = os.path.join(DATA_DIR, rel_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f'汇合表不存在: {path} (先跑 python run_all.py --merge)')
    return pd.read_csv(path, sep='\t', **kwargs)


# ---- Dynamic column width --------------------------------------------
#
# Column families like `INSDC_Nuc_ID_1..N` / `Enzyme_1..N` / `PMID_1..N` have a
# **column count that follows the data**. Hard-coding the cap is "silent data
# loss": the excess raises no error, it simply does not exist.
# Measured: all three caps were **already maxed out with real data** in the last
# column --
#   etl_edges `range(1, 46)`      -> the committed pairs table's max_n is exactly 45
#   etl_search_index `range(1, 25)`/`range(1, 12)` -> last column has 1 real row each
#   etl_master, the same two      -> same as above
# So always derive from the header. This holds the **single** implementation, so a few
# modules do not each write one and drift apart.

def indexed_groups(columns, stem):
    """Column family `X_1..X_N` -> range(1, N+1). An empty range when the family is
    absent."""
    numbers = []
    pattern = re.compile(rf'^{re.escape(stem)}_(\d+)$')
    for col in columns:
        m = pattern.match(col)
        if m:
            numbers.append(int(m.group(1)))
    return range(1, max(numbers, default=0) + 1)


def indexed_width(columns, *stems):
    """The largest N among these column families. One segment may have only prot id
    and no nuc id, so take the max across several families."""
    return max((max(indexed_groups(columns, stem), default=0) for stem in stems), default=0)
