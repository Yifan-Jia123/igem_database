"""
Regenerate the GO child table (uniprotkb_go.tsv) from the unified raw TSV.
Format: Entry, GO ID, GO Term, GO Link

数据来源: 统一原始 TSV 的 'Gene Ontology (biological process)' 列 (go_p 字段)。
旧 uniprotkb_go.tsv 只存生物过程 (BP) 方面的 GO 词条, 且该列正是 BP-only:
  - 真实行: go_p 解析结果与旧表 2698 个真实行逐格 0 差异
  - 空占位行: 无 BP GO 的酶补一行 (GO ID/Term/Link 全空), 保证每酶至少一行, 与旧表结构一致
  - 注意: 旧表个别无 GO 酶有 2~7 行空占位 (历史构建脚本按当时 API GO 引用数发空行,
    源数据已变, 现统一为每酶一行空占位)。rebuild_master 读子表时会过滤 GO ID 为空的行。

不再打 UniProt API: GO 交叉引用已含在导出的 go_p 列, 符合 'API 只补导出没有的' 策略,
且离线解析确定性更高。

用法: python fetch_go.py [输入原始TSV] [输出文件]
"""
import csv
import re
import sys

# 用法: python fetch_go.py [输入原始TSV] [输出文件]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'output_uniprot_unified.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_go.tsv'

# 列头固定名 (UniProt TSV 里 go_p 字段的显示名)
GO_COL = 'Gene Ontology (biological process)'

# ---- collect entries and their BP GO terms from the raw TSV ----
go_by_entry = {}
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        e = row['Entry']
        gos = []
        col = row.get(GO_COL, '') or ''
        for t in col.split(';'):
            t = t.strip()
            if not t:
                continue
            m = re.search(r'\[(GO:\d+)\]$', t)
            term = t[:m.start()].strip() if m else t
            goid = m.group(1) if m else ''
            if goid:
                gos.append({
                    'go_id': goid,
                    'term': term,
                    'link': f'https://www.ebi.ac.uk/QuickGO/term/{goid}',
                })
        go_by_entry[e] = gos

# ---- write: real GO rows + 每酶至少一行(无 GO 的酶补一行空占位, 与旧表结构一致) ----
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Entry', 'GO ID', 'GO Term', 'GO Link'])
    for entry in go_by_entry:
        gos = go_by_entry[entry]
        if gos:
            for g in gos:
                w.writerow([entry, g['go_id'], g['term'], g['link']])
        else:
            w.writerow([entry, '', '', ''])  # 空占位: 无 BP GO 的酶(行序与原始表一致)

# ---- stats ----
total_go = sum(len(v) for v in go_by_entry.values())
with_go = sum(1 for v in go_by_entry.values() if v)
empty = len(go_by_entry) - with_go
print(f'\nDone: {len(go_by_entry)} entries, {total_go} real GO terms (BP-only), '
      f'{with_go} entries with GO, {empty} empty placeholder rows -> {OUTPUT}')
