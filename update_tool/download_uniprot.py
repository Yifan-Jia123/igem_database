"""
下载 UniProt 统一全列表 (单张 TSV, 取代三个分散导出 0710/0712/0716)。

数据来源: UniProt REST stream 端点。
19 列 = 三个导出列的并集 (与旧三导出列头完全一致), 外加:
  Reviewed  —— 派生 'Source' 列的依据 (reviewed -> swiss_prot, unreviewed -> trembl)
  Source    —— 本脚本追加, 标记本行来自哪个来源分段

**一次下载整个检索集, 再按 Reviewed 列在本地拆成两个分段文件**:
  python download_uniprot.py                      # (terpene) -> _src/swiss_prot/ + _src/trembl/
  python download_uniprot.py --source=trembl      # 同上, 但只写 trembl 那一个文件 (仍会下全量)
  python download_uniprot.py --root=./_pilot/src  # 换输出根目录

**为什么检索词不按来源切分** (旧做法是 `(terpene) AND reviewed:true` / `reviewed:false` 各下一次):
两次下载 = **两个时间窗**。跨窗口的升/降级会让同一个 accession 同时落进两段、或被两段都漏掉,
而本方案的前提是「分段必须作为一组重新产出」(见 plan), 跨来源裁决只是兜底。
一个检索词 = 一个快照, 这个前提就成了**脚本强制的**, 不再依赖「两个词记得一起改」这种纪律。

单文件模式 (试点切片用, 不拆分; --out 与 --source 配合):
  python download_uniprot.py --out=FILE --source=swiss_prot \
         --query='(terpene) AND reviewed:true AND length:[200 TO 400]'

隔离原则: 输出写到本文件夹的 _src/ 下, 不碰 ../ 原始文件。
换新数据时: 直接重跑本脚本覆盖输出, 再把 run_all.py 指向新文件即可。
"""
import csv
import os
import requests
import sys
import time

# 来源登记在 source_registry.py 里, 与 run_all.py 共用同一份 —— 两处各写一份会漂移,
# 而漂移的后果是 --merge 的安全阀误判「来源齐了」(见该文件 docstring)。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_registry import ALL_QUERY, SOURCES  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# 默认分段根目录与 run_all.py **同一基准**: 相对**脚本目录**, 不是相对 cwd。
# run_all.py:62 用的就是 os.path.join(HERE, '_src')。两者不一致的话,
# 从仓库根跑 `python update_tool/download_uniprot.py` 会写到 ../_src/,
# 而 run_all.py 读的是 update_tool/_src/ —— 于是磁盘上出现两份 _src,
# 各自看起来都"齐了", 而流程在读另一份。这与本项目一路在防的是同一类缺陷。
DEFAULT_ROOT = os.path.join(HERE, '_src')
ROOT = DEFAULT_ROOT
OUT = None            # 单文件模式的目标路径; 给了它就**不拆分**
SOURCE = None         # 拆分模式: 只写这一个来源的文件; 单文件模式: Source 列的兜底值
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
        ROOT = os.path.abspath(a[len('--root='):].strip())   # 与 run_all 的 --src-root 同样按 cwd 解析
    elif a.startswith('--out='):
        OUT = a[len('--out='):].strip()
    elif a.startswith('--'):
        sys.exit(f'未知参数 {a!r}')
    else:
        OUT = a          # 位置参数 = 单文件输出路径 (旧用法, 保持可用)

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

# 确认总条数
try:
    cnt = requests.get('https://rest.uniprot.org/uniprotkb/search',
                       params={'query': QUERY, 'size': 1},
                       headers={'Accept': 'application/json'}, timeout=60)
    total = cnt.headers.get('x-total-results', '?')
    print(f'Total entries: {total}')
except Exception as e:
    print(f'WARN: count check failed ({e}), proceeding anyway')

# 流式下载 (stream 端点无分页上限)。先落到临时文件, 再追加 Source 列写正式输出。
# 临时文件放在输出目录里 —— 同一个文件系统, 免得跨盘复制。
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
        # 逐块写文件
        with open(TMP, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        break
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f'{type(e).__name__}, retry {attempt+1}/5')
        time.sleep(5 * (attempt + 1))
else:
    sys.exit('Download FAILED after 5 retries')

# ---- 追加 'Source' 列, 并按来源分别落盘 ----
# 逐行流式处理: 94k 行 x 20 列全量读进内存是几百 MB, 没必要。
# Source 由 'Reviewed' 列派生 (数据驱动) —— 这正是「不按 reviewed 切检索词」可行的依据。
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
            # 拆分模式没有 Reviewed 就无从拆分。这里**硬报错**而不是把全部行塞进一个分段:
            # 后者产出的正是 sources.read_segmented 会拒绝的「文件名后缀与 Source 列不符」,
            # 与其让它在下游炸, 不如在下载这一步说清楚。
            sys.exit("拆分模式需要 'Reviewed' 列来派生 Source, 但下载结果里没有。\n"
                     "  确认 FIELDS 含 'reviewed'; 或改用 --out=<文件> 单文件模式。")

        # ---- 校验全过之后才创建输出文件 ----
        # 早于这里打开会留下 **0 字节** 的输出文件, 而 run_all.py 的存在性检查是
        # `os.path.exists` —— 0 字节文件会通过那道检查, 然后被当成分段文件读下去。
        # 那正是本项目一路在防的「看着都齐、其实没有数据」, 所以创建必须放在校验之后。
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
                src = SOURCE          # 单文件模式的兜底 (拆分模式已在上面报错)
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
    # 提前 sys.exit 时也要收尾 —— 否则留下几十 MB 的临时文件,
    # 而它跟正式输出同目录, 很容易被误当成产物。
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
        # 不带 --source 时, 写出数必须等于下载数。少一行都说明分派逻辑漏了。
        sys.exit(f'内部错误: 写出 {sum(written.values())} 行 != 下载 {n_rows} 行')
