"""
Download the UniProt unified full list (a single TSV, replacing the three scattered
exports 0710/0712/0716).

Data source: the UniProt REST stream endpoint.
19 columns = the union of the three export columns (matching the old three-export header
exactly), plus:
  Reviewed  -- the basis for the derived 'Source' column (reviewed -> swiss_prot,
               unreviewed -> trembl)
  Source    -- appended by this script, marking which source segment the row came from

**Download the whole query set once, then split it locally into two segment files by the
Reviewed column**:
  python download_uniprot.py                      # (terpene) -> _src/swiss_prot/ + _src/trembl/
  python download_uniprot.py --source=trembl      # same, but writes only the trembl file (still downloads the full set)
  python download_uniprot.py --root=./_pilot/src  # change the output root

**Why the query is not split by source** (the old way ran `(terpene) AND reviewed:true` and
`reviewed:false` separately): two downloads = **two time windows**. A promotion/demotion
across the windows lets the same accession land in both segments, or be missed by both, while
this approach's premise is that "segments must be re-produced as a group" (see the plan);
cross-source arbitration is only a fallback. One query = one snapshot, and that premise
becomes **enforced by the script**, no longer relying on the discipline of "remember to
change both queries together".

Single-file mode (for pilot slices, no splitting; --out works with --source):
  python download_uniprot.py --out=FILE --source=swiss_prot \
         --query='(terpene) AND reviewed:true AND length:[200 TO 400]'

Isolation principles: output is written under this folder's _src/, never touching the
../ original files. To switch to new data: just re-run this script to overwrite the output,
then point run_all.py at the new file.
"""
import csv
import os
import requests
import sys
import time

# Sources are registered in source_registry.py, shared with run_all.py -- writing a copy in
# each place would drift, and the consequence of drift is --merge's safety valve wrongly
# deciding "sources are complete" (see that file's docstring).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_registry import ALL_QUERY, SOURCES  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# The default segment root uses the **same base as run_all.py**: relative to the **script
# directory**, not to the cwd. run_all.py:62 uses os.path.join(HERE, '_src'). If the two
# disagree, running `python update_tool/download_uniprot.py` from the repo root writes to
# ../_src/, while run_all.py reads update_tool/_src/ -- so two _src appear on disk, each
# looking "complete", while the pipeline reads the other one. This is the same class of
# defect this project defends against throughout.
DEFAULT_ROOT = os.path.join(HERE, '_src')
ROOT = DEFAULT_ROOT
OUT = None            # single-file mode's target path; if given, **no splitting**
SOURCE = None         # split mode: write only this source's file; single-file mode: fallback value for the Source column
QUERY_OVERRIDE = None
FIELDS = ('accession,id,protein_name,organism_name,gene_primary,kinetics,cc_function,'
          'rhea,go_p,sequence,ft_var_seq,ec,cc_catalytic_activity,cc_alternative_products,'
          'lit_pubmed_id,lit_doi_id,xref_geneid,length,mass,'
          'reviewed')
for a in sys.argv[1:]:
    if a.startswith('--query='):
        QUERY_OVERRIDE = a[len('--query='):]
    elif a.startswith('--source='):
        SOURCE = a[len('--source='):].strip()
    elif a.startswith('--fields='):
        FIELDS = a[len('--fields='):]
    elif a.startswith('--root='):
        ROOT = os.path.abspath(a[len('--root='):].strip())   # parsed relative to cwd, same as run_all's --src-root
    elif a.startswith('--out='):
        OUT = a[len('--out='):].strip()
    elif a.startswith('--'):
        sys.exit(f'未知参数 {a!r}')
    else:
        OUT = a          # positional arg = single-file output path (old usage, kept working)

if SOURCE is not None and SOURCE not in SOURCES:
    sys.exit(f'--source 必须是 {SOURCES} 之一, 收到 {SOURCE!r}')

QUERY = QUERY_OVERRIDE if QUERY_OVERRIDE else ALL_QUERY

URL = 'https://rest.uniprot.org/uniprotkb/stream'
params = {'query': QUERY, 'format': 'tsv', 'fields': FIELDS}

print(f'Query : {QUERY}')
print(f'Fields: {len(FIELDS.split(","))} columns')
if OUT:
    print(f'Mode  : 单文件 -> {OUT}' + (f'  (Source 兜底 = {SOURCE})' if SOURCE else ''))
else:
    print(f'Mode  : 拆分 -> {ROOT}/<source>/output_uniprot_unified.tsv'
          f'  (写 {[SOURCE] if SOURCE else list(SOURCES)})')

# Confirm the total entry count
try:
    cnt = requests.get('https://rest.uniprot.org/uniprotkb/search',
                       params={'query': QUERY, 'size': 1},
                       headers={'Accept': 'application/json'}, timeout=60)
    total = cnt.headers.get('x-total-results', '?')
    print(f'Total entries: {total}')
except Exception as e:
    print(f'WARN: count check failed ({e}), proceeding anyway')

# Streaming download (the stream endpoint has no pagination limit). Write to a temp file
# first, then append the Source column and write the real output.
# The temp file lives in the output directory -- same filesystem, avoiding a cross-disk copy.
BASE_DIR = os.path.dirname(os.path.abspath(OUT)) if OUT else os.path.abspath(ROOT)
os.makedirs(BASE_DIR, exist_ok=True)
TMP = os.path.join(BASE_DIR, '.uniprot_download.tsv.raw')

for attempt in range(5):
    try:
        r = requests.get(URL, params=params, stream=True, timeout=600)
        if r.status_code != 200:
            print(f'HTTP {r.status_code}, retry {attempt+1}/5')
            time.sleep(5 * (attempt + 1))
            continue
        # Write the file chunk by chunk
        with open(TMP, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        break
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f'{type(e).__name__}, retry {attempt+1}/5')
        time.sleep(5 * (attempt + 1))
else:
    sys.exit('Download FAILED after 5 retries')

# ---- Append the 'Source' column and write out by source ----
# Streaming, row by row: reading all 94k rows x 20 columns into memory is several hundred
# MB, unnecessary. Source is derived from the 'Reviewed' column (data-driven) -- which is
# precisely what makes "not splitting the query by reviewed" workable.
handles = []
single = None
sinks = {}
n_rows = 0
src_counts = {}
written = {}
skipped = {}

try:
    with open(TMP, 'r', encoding='utf-8', newline='') as fin:
        reader = csv.reader(fin, delimiter='\t')
        header = next(reader, None)
        if header is None:
            sys.exit('Downloaded file is empty')

        if 'Source' in header:
            sys.exit("下载结果里已经有 'Source' 列, 说明 FIELDS 里混入了同名列")
        rv_idx = header.index('Reviewed') if 'Reviewed' in header else None
        if rv_idx is None and not OUT:
            # Split mode cannot split without Reviewed. This **hard-errors** rather than
            # dumping all rows into one segment: the latter produces exactly the "file-name
            # suffix disagrees with the Source column" that sources.read_segmented rejects,
            # and it is better to say so at the download step than to let it blow up downstream.
            sys.exit("拆分模式需要 'Reviewed' 列来派生 Source, 但下载结果里没有。\n"
                     "  确认 FIELDS 含 'reviewed'; 或改用 --out=<文件> 单文件模式。")

        # ---- create the output file only after all checks pass ----
        # Opening it any earlier leaves a **0-byte** output file, and run_all.py's existence check is
        # `os.path.exists` -- a 0-byte file passes that check, then gets read as a segment file.
        # That is exactly the "looks complete but has no data" this project guards against, so creation must come after the checks.
        if OUT:
            fh = open(OUT, 'w', encoding='utf-8', newline='')
            handles.append(fh)
            single = csv.writer(fh, delimiter='\t', lineterminator='\n')
        else:
            for s in ([SOURCE] if SOURCE else list(SOURCES)):
                p = os.path.join(ROOT, s, 'output_uniprot_unified.tsv')
                os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
                fh = open(p, 'w', encoding='utf-8', newline='')
                handles.append(fh)
                sinks[s] = csv.writer(fh, delimiter='\t', lineterminator='\n')

        hdr_out = header + ['Source']
        if OUT:
            single.writerow(hdr_out)
        else:
            for w in sinks.values():
                w.writerow(hdr_out)

        for row in reader:
            if rv_idx is not None and rv_idx < len(row):
                src = 'swiss_prot' if row[rv_idx].strip().lower() == 'reviewed' else 'trembl'
            else:
                src = SOURCE          # single-file mode's fallback (split mode already errored above)
            if src not in SOURCES:
                src = SOURCE
            n_rows += 1
            src_counts[src] = src_counts.get(src, 0) + 1
            if OUT:
                single.writerow(row + [src])
            elif src in sinks:
                sinks[src].writerow(row + [src])
                written[src] = written.get(src, 0) + 1
            else:
                skipped[src] = skipped.get(src, 0) + 1
finally:
    # Also clean up on an early sys.exit -- otherwise tens of MB of temp file are left behind,
    # and since it sits in the same directory as the real output, it is easily mistaken for a product.
    for fh in handles:
        fh.close()
    if os.path.exists(TMP):
        os.remove(TMP)

print(f'\nDone: 下载 {n_rows} 行, {len(header)+1} 列')
for s in sorted(src_counts):
    print(f'  Source={s}: {src_counts[s]}')
if OUT:
    print(f'  -> {OUT}')
    if len(src_counts) > 1:
        print(f'  NOTE: 单文件模式下载了多个来源 {sorted(src_counts)} —— '
              f'这个文件不能直接当分段文件用 (sources.read_segmented 会拒绝)。')
else:
    for s in sorted(sinks):
        print(f'  -> {os.path.join(ROOT, s, "output_uniprot_unified.tsv")}  ({written.get(s, 0)} 行)')
    if skipped:
        print(f'  NOTE: 未写出 {skipped} —— --source 只写一个来源; '
              f'数据已下全, 不带 --source 重跑即补齐另一个来源的文件。')
    elif sum(written.values()) != n_rows:
        # Without --source, the number of rows written must equal the number downloaded. One row short means the dispatch logic dropped something.
        sys.exit(f'内部错误: 写出 {sum(written.values())} 行 != 下载 {n_rows} 行')
