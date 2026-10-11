"""ETL Step 2: the enzyme table -- replace by source + IDs stay permanent and unreused.

## Two invariants (plan Phase 2.1 / 2.2)

1. **IDs stay permanent and are never reused.** The original implementation assigned
   positional IDs `f"ENZ{i+1:06d}"` -- the moment the base set changed (996 -> 96k) the
   whole database shifted, and `enzyme_id` is referenced by the **FKs of 7 tables**.
   Now every ID comes from `enzyme_id_map`: a hit reuses it / a miss takes `MAX+1` / a
   vanished entry only gets `retired_at` and its ID is **not reclaimed**. Any ENZ number
   ever issued forever points at the same biological entity.

2. **Add-only + insert-only.** Per source, replace the whole segment (after `DELETE WHERE
   source_type=<s>`, reload), **not** "read existing -> filter out those already present".
   The latter means "skip if it already exists": new names and new GO terms for an existing
   enzyme never get in.

## Base set = names_split (all entries), not rhea_summary

The original implementation took enzymes from `uniprotkb_rhea_summary.tsv` -- that is
"enzymes that have a reaction". The 528 SwissProt enzymes that have only a sequence and no
reaction annotation were therefore **silently dropped**. names_split contains all entries.

⚠️ **Cannot be switched to `uniprotkb_master.tsv`**: it has 951 columns but **no `Organism` /
`Protein name`**, which would KeyError.

## Child tables must be cleared first (call-order constraint)

The child tables of `enzyme` (gene / gene_sequence_link / evidence / enzyme_go /
enzyme_isoform / enzyme_reaction_edge / search_index) **all lack `ON DELETE CASCADE`**.
So this module's `DELETE FROM enzyme` must happen **after** etl_run.py's purge phase, or it
is a direct foreign-key error. Running this module on its own requires purge to have run
first.
"""
import json
import os
import re
import sys

import pandas as pd
import requests
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL  # noqa: E402
from sources import SOURCE_PRIORITY, read_segmented  # noqa: E402

engine = create_engine(DB_URL)

NAMES_FILE = 'for_enzyme_detail/child_tables/uniprotkb_names_split.tsv'
RHEA_SUMMARY_FILE = 'for_enzyme_reaction_card/uniprotkb_rhea_summary.tsv'

# source -> review_status. Written as a mapping rather than if/else, and an unknown source
# errors out -- "guess a default" would silently make a new source's review status wrong.
SOURCE_TO_REVIEW = {'swiss_prot': 'official', 'trembl': 'pending'}

# Cap on 302 resolution for vanished accessions. See _resolve_redirects.
REDIRECT_LIMIT = 500
_ACC_IN_LOCATION = re.compile(r'/uniprotkb/([A-Za-z0-9]+)')


def _coalesce(*series):
    """Take the first non-empty value in order -- **treating an empty string as empty too**.

    The original implementation used `fillna`, and `fillna` only fills NaN, **not empty
    strings**. TrEMBL names come in an empty-string form, so primary_name would land as an
    empty string, bypass every fallback, and become an enzyme with no name.
    """
    out = None
    for s in series:
        cur = s.fillna('').astype(str).str.strip()
        out = cur if out is None else out.where(out != '', cur)
    return out


def load_entries(only=None):
    """names_split is the base set (all entries); left-join rhea_summary for Protein name /
    Organism.

    names_split is one entry per row; rhea_summary is one reaction per row -> collapse by
    entry before joining.
    """
    names = read_segmented(NAMES_FILE, only=only, dtype=str)
    rhea = read_segmented(RHEA_SUMMARY_FILE, only=only, dtype=str)

    for col in ('Entry', 'Recommended name', 'Source'):
        if col not in names.columns:
            raise KeyError(f'{NAMES_FILE} 缺列 {col!r}; 实际列: {list(names.columns)}')
    if 'Protein name' not in rhea.columns:
        raise KeyError(f'{RHEA_SUMMARY_FILE} 缺列 "Protein name"')

    # ⚠️ Here we **must not** silently de-duplicate by Entry. There used to be a
    # `drop_duplicates(subset='Entry')` here; it flattened the case "the same accession
    # appears in two segments" in place -- keeping the **segment read first** (i.e. the
    # SOURCES order), printing nothing. The result happened to match "SwissProt first", so
    # it never surfaced; but it also turned dedupe_cross_source's explicit priority and
    # conflict report into dead code that never runs, so one rule had two implementations
    # and the one actually in effect was implicit in the concat order.
    # Now it is all decided by dedupe_cross_source: explicit priority + per-row printing.
    rhea_by_entry = rhea.drop_duplicates(subset='Entry').set_index('Entry')

    entry = names['Entry'].astype(str).str.strip()

    def _from_rhea(col):
        if col not in rhea_by_entry.columns:
            return pd.Series([''] * len(entry), index=entry.index)
        return pd.Series(rhea_by_entry[col].reindex(entry).values, index=entry.index)

    source = names['Source'].astype(str).str.strip()
    review = source.map(SOURCE_TO_REVIEW)
    if review.isna().any():
        bad = sorted(set(source[review.isna()]))
        raise ValueError(
            f'未知来源 {bad}: 无法确定 review_status。已登记映射 {SOURCE_TO_REVIEW}。\n'
            '  新增来源时要在 SOURCE_TO_REVIEW 里补上, 不要让它落到默认值。'
        )

    organism = _coalesce(names.get('Organism', pd.Series([''] * len(entry), index=entry.index)),
                         _from_rhea('Organism'))

    enzyme = pd.DataFrame({
        'uniprot_id': entry.values,
        'primary_name': _coalesce(names.get('Recommended name',
                                            pd.Series([''] * len(entry), index=entry.index)),
                                  _from_rhea('Protein name'),
                                  entry).values,
        'organism_name': organism.values,
        'secondary_names': names.get('Alternative names',
                                     pd.Series([''] * len(entry), index=entry.index)).values,
        'source_type': source.values,
        'review_status': review.values,
    })
    enzyme = enzyme[enzyme['uniprot_id'] != ''].reset_index(drop=True)
    return enzyme


def dedupe_cross_source(enzyme):
    """When the same accession appears in two segments, the higher-priority source wins and
    the other segment's row is dropped.

    Otherwise `enzyme.uniprot_id`'s UNIQUE would error out directly. A reviewed-status
    change (upgrade/downgrade) happening between the two segments' download windows is
    exactly where this arises -- the same accession is one entity, so **drop rather than
    keep one row each**, and what is dropped is the lower-priority source's copy.

    Priority is taken from the order of `source_registry.SOURCES` (via
    sources.SOURCE_PRIORITY), **not written a second time here** -- two copies would drift
    when a source is added, and the drift shows up as "silently adjudicated by the wrong
    copy".

    This path is the **only** decision point: upstream load_entries no longer pre-dedupes by
    Entry, so duplicate rows are guaranteed to show up here and conflicts are guaranteed to
    be printed (see the note in load_entries).
    """
    dup = enzyme[enzyme.duplicated(subset='uniprot_id', keep=False)]
    if dup.empty:
        return enzyme, 0

    enzyme = enzyme.copy()
    enzyme['_prio'] = enzyme['source_type'].map(SOURCE_PRIORITY)
    # The old code was `.fillna(99)` -- an unknown source silently sorted last. Better to
    # error: a source value not registered in SOURCES cannot be obtained anyway (the
    # segmented path validates against the registry), so its appearance means something
    # bypassed that.
    if enzyme['_prio'].isna().any():
        bad = sorted(set(enzyme.loc[enzyme['_prio'].isna(), 'source_type']))
        raise ValueError(f'未知来源 {bad}: 无法裁决跨来源冲突。已登记 {list(SOURCE_PRIORITY)}。')
    enzyme = enzyme.sort_values(['uniprot_id', '_prio'], kind='stable')
    kept = enzyme.drop_duplicates(subset='uniprot_id', keep='first').drop(columns='_prio')
    dropped = len(enzyme) - len(kept)

    losers = enzyme[enzyme.duplicated(subset='uniprot_id', keep='first')]
    if not losers.empty:
        for acc, grp in losers.groupby('uniprot_id'):
            print(f'  [来源冲突] {acc}: 保留 {kept.loc[kept.uniprot_id == acc, "source_type"].iloc[0]}, '
                  f'剔除 {"+".join(sorted(set(grp.source_type)))}')
    print(f'  enzyme: 跨来源重复 accession {len(dup)} 行, 剔除 {dropped} 行 (SwissProt 优先)')
    return kept.reset_index(drop=True), dropped


def load_id_maps():
    """enzyme_id_map / enzyme_alias_map -> two dicts. These two tables never take part in
    DELETE."""
    with engine.connect() as conn:
        m = pd.read_sql("SELECT uniprot_id, enzyme_id FROM enzyme_id_map", conn)
        a = pd.read_sql("SELECT secondary_accession, enzyme_id FROM enzyme_alias_map", conn)
    id_map = {str(k).strip(): str(v).strip() for k, v in zip(m.uniprot_id, m.enzyme_id)}
    alias_map = {str(k).strip(): str(v).strip() for k, v in zip(a.secondary_accession, a.enzyme_id)}
    return id_map, alias_map


def _resolve_redirect(acc):
    """UniProt returns a 302 to the current primary accession for a retired accession. None
    when unavailable.

    Measured 4/4 resolved in one hop (e.g. B6SYF3 -> A0A1D6EFT8).
    """
    try:
        r = requests.get(f'https://rest.uniprot.org/uniprotkb/{acc}',
                         allow_redirects=False, timeout=15)
    except Exception:
        return None
    if r.status_code not in (301, 302, 303, 307, 308):
        return None
    m = _ACC_IN_LOCATION.search(r.headers.get('Location', ''))
    return m.group(1) if m else None


def resolve_vanished(id_map, present_accessions, only=None):
    """Find the new ID for an accession that "had an ID before but is gone from this run's
    data".

    Here we **only request on vanished IDs** (<= |id map|, actually tiny), rather than
    requesting for every new entry that fails to match -- the latter equals 94k HTTP
    requests on the first full expansion (~5 hours). Asking the reverse question, "is the
    number we know still there", is both sufficient and bounded.

    Hit a new ID that is present in the current data -> write enzyme_alias_map, and the
    original number is reused thereafter.
    """
    if only:
        # When reloading only one source, the other source's entries are still in the
        # database, so "vanished" cannot be decided.
        print('  [redirect] --source 模式跳过退休号解析 (需全量视图)')
        return 0

    vanished = sorted(set(id_map) - present_accessions)
    if not vanished:
        return 0
    if len(vanished) > REDIRECT_LIMIT:
        print(f'  [redirect] 消失的编号有 {len(vanished)} 个, 超过上限 {REDIRECT_LIMIT}, '
              f'本步跳过 (逐个发 HTTP 请求代价过高)。编号仍会保留, 只是不接续。')
        return 0

    print(f'  [redirect] {len(vanished)} 个编号对应的 accession 已不在数据里, 逐个解析 302 ...')
    resolved = 0
    for acc in vanished:
        new_acc = _resolve_redirect(acc)
        if not new_acc or new_acc == acc:
            continue
        if new_acc in present_accessions:
            print(f'    {acc} -> {new_acc} (编号接续)')
            resolved += 1
        else:
            print(f'    {acc} -> {new_acc} (新号也不在数据里, 视为退休)')
    return resolved


def assign_ids(enzyme, id_map, alias_map):
    """Assign enzyme_id. A hit reuses it / a secondary hit reuses it / a miss takes
    MAX+1. **Never fill holes**."""
    used = set(id_map.values())
    nxt = 1 + max((int(e[3:]) for e in used
                   if e.startswith('ENZ') and e[3:].isdigit()), default=0)

    ids, new_rows = [], []
    reused = by_alias = allocated = 0
    for acc in enzyme['uniprot_id']:
        eid = id_map.get(acc)
        if eid:
            reused += 1
        else:
            eid = alias_map.get(acc)
            if eid:
                by_alias += 1
            else:
                eid = f'ENZ{nxt:06d}'
                nxt += 1
                allocated += 1
            new_rows.append((acc, eid))     # alias-continued entries need the primary-ID map too
        ids.append(eid)

    # The newly allocated ID range is [nxt - allocated, nxt - 1] -- the assertion below also
    # uses nxt - allocated. An earlier version added a stray +1 here, so the log showed
    # "starting at ENZ000002", which did not match the actual starting ID in the database.
    print(f'  enzyme_id: 沿用 {reused}, 经别名接续 {by_alias}, 新分配 {allocated}'
          f'{f" (从 ENZ{nxt - allocated:06d} 到 ENZ{nxt - 1:06d})" if allocated else ""}')
    if allocated and (nxt - allocated) <= max((int(e[3:]) for e in used
                                              if e.startswith('ENZ') and e[3:].isdigit()), default=0):
        raise AssertionError('新分配的编号没有大于历史 MAX —— 违反「编号只增不减」')
    return ids, new_rows


def write_id_map(new_rows, present_accessions, only=None):
    """Write back the ID table + mark retired_at on entries that vanished this run (no row
    deletion, no ID reclamation)."""
    if new_rows:
        with engine.begin() as conn:
            conn.execute(
                text('INSERT INTO enzyme_id_map (uniprot_id, enzyme_id, first_seen) '
                     'VALUES (:u, :e, NOW()) '
                     'ON DUPLICATE KEY UPDATE enzyme_id = VALUES(enzyme_id), retired_at = NULL'),
                [{'u': u, 'e': e} for u, e in new_rows],
            )
        print(f'  enzyme_id_map: 新增 {len(new_rows)} 行')

    if only:
        # Same reason as resolve_vanished: when reloading only one source, present_accessions
        # holds only that source, so the other source's entries never enter this view and
        # would be wholesale misjudged as "vanished from the data" -- yet they are still in
        # the database and their IDs still in use. Deciding "vanished" requires the full
        # view, so the whole step is skipped.
        # Measured (2026-09-19): running only --source=trembl stamps retired_at on all 1,535
        # swiss_prot entries (all 1,535 of which were still in the enzyme table at the
        # time). A full re-run clears them back, but "a single-source refresh must not touch
        # any row of the other source" requires not touching this at all.
        print(f'  enzyme_id_map: --source={only} 模式跳过 retired_at 维护 (需全量视图)')
        return

    with engine.connect() as conn:
        known = [r[0] for r in conn.execute(text('SELECT uniprot_id FROM enzyme_id_map'))]

    gone = [a for a in known if a not in present_accessions]
    back = [a for a in known if a in present_accessions]

    with engine.begin() as conn:
        if gone:
            conn.execute(text(_retire_sql(gone, True)),
                         {f'p{i}': a for i, a in enumerate(gone)})
        if back:
            # The entry came back (downgraded then upgraded again): clear the retirement
            # mark.
            conn.execute(text(_retire_sql(back, False)),
                         {f'p{i}': a for i, a in enumerate(back)})

    if gone:
        print(f'  enzyme_id_map: {len(gone)} 个条目消失 -> 打 retired_at (编号保留, 不回收)')
    if back:
        print(f'  enzyme_id_map: {len(back)} 个条目回来 -> 撤掉 retired_at')


def _retire_sql(accessions, retire):
    """An IN list cannot use a bound parameter directly; expand placeholders by length (IN
    does not accept a single list parameter)."""
    placeholders = ','.join(f':p{i}' for i in range(len(accessions)))
    if retire:
        return ('UPDATE enzyme_id_map SET retired_at = NOW() '
                f'WHERE retired_at IS NULL AND uniprot_id IN ({placeholders})')
    return ('UPDATE enzyme_id_map SET retired_at = NULL '
            f'WHERE retired_at IS NOT NULL AND uniprot_id IN ({placeholders})')


def load_enzymes(only=None):
    """Replace the enzyme table by source. Before calling, the child tables must already be
    cleared by etl_run.purge_source()."""
    enzyme = load_entries(only=only)
    print(f'  enzyme: 读入 {len(enzyme)} 条目 (基集 = names_split)')
    enzyme, _ = dedupe_cross_source(enzyme)

    id_map, alias_map = load_id_maps()
    print(f'  enzyme_id_map: {len(id_map)} 行, enzyme_alias_map: {len(alias_map)} 行')

    present = set(enzyme['uniprot_id'])
    resolve_vanished(id_map, present, only=only)

    ids, new_rows = assign_ids(enzyme, id_map, alias_map)
    enzyme['enzyme_id'] = ids
    enzyme['secondary_names'] = enzyme['secondary_names'].fillna('').apply(
        lambda s: json.dumps([x.strip() for x in str(s).split(';') if x.strip()])
    )

    sources = sorted(set(enzyme['source_type']))
    cols = ['enzyme_id', 'uniprot_id', 'primary_name', 'organism_name',
            'secondary_names', 'source_type', 'review_status']

    with engine.begin() as conn:
        for s in sources:
            n = conn.execute(text('DELETE FROM enzyme WHERE source_type = :s'), {'s': s}).rowcount
            print(f'  enzyme: 删除 source_type={s} 的 {n} 行 (整段替换)')
        enzyme[cols].to_sql('enzyme', conn, if_exists='append', index=False)

    print(f'  enzyme: 插入 {len(enzyme)} 行')
    for s in sources:
        print(f'     {s}: {int((enzyme.source_type == s).sum())}')
    write_id_map(new_rows, present, only=only)


if __name__ == '__main__':
    load_enzymes()


def run(only=None):
    load_enzymes(only=only)
