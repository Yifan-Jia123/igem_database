import csv
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protein_name import split_recommended_name  # noqa: E402

# 用法: python parse_names.py [原始TSV] [输出parsed.tsv]
INPUT = sys.argv[1] if len(sys.argv) > 1 else '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'output_parsed.tsv'

rows = []
with open(INPUT, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    fieldnames = reader.fieldnames
    for row in reader:
        raw = row.get('Protein names', '')

        # ---- 1. 提取推荐名 ----
        # 切分逻辑已抽到 protein_name.py, 与 build_rhea_summary / build_enzyme_merged 共用:
        # 三处内联过各自的版本, 行为还不一致(只有这里去掉 [Includes:]), 改一处另两处不跟着变。
        rec_name = split_recommended_name(raw)

        # ---- 2. 提取所有顶级括号内的内容（处理嵌套括号如 ((2E,6E)-farnesyl...) ----
        paren_items = []
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == '(':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0 and start >= 0:
                    paren_items.append(raw[start+1:i])
                    start = -1

        # ---- 3. 分类：EC 编号 vs 别名 ----
        # 立体化学前缀如(-)、(+) 、(E)、(4S)、(E,E)、(2Z,6E)等会被深度解析器
        # 误当成括号内容抓出来，这些不是酶名，需要过滤掉。
        def _is_stereochem(s):
            s = s.strip()
            if not s:
                return True
            # 纯光学旋转标记: -, +, ±
            if re.match(r'^[\-+±]$', s):
                return True
            # 纯数字：化学命名中的位置编号，如 2,10(14)-diene 中的 14
            if re.match(r'^\d{1,3}$', s):
                return True
            # 立体描述符: E, Z, R, S, 可能有编号如 4S, 2E,6E, 3R:5R
            if re.match(r'^\d*[ERSZ]([,:]\d*[ERSZ])*$', s):
                return True
            return False

        ec_list = []
        alt_names = []
        for item in paren_items:
            if re.match(r'EC\s+[\d\-n]+\.[\d\-n]+\.[\d\-n]+\.[\d\-n]+', item):
                ec_list.append(item)
            elif not _is_stereochem(item):
                alt_names.append(item)

        row['Recommended name'] = rec_name
        row['Alternative names'] = '; '.join(alt_names)
        row['EC numbers'] = '; '.join(ec_list)
        rows.append(row)

# 重新排序列
# 'Source' 由 download_uniprot.py 写入 RAW 统一表, 这里透传下去 (extrasaction='ignore'
# 意味着不列出来的列会被丢掉 —— 漏掉它会让下游所有脚本都拿不到来源)。
new_fields = ['Entry', 'Recommended name', 'Alternative names', 'EC numbers',
              'Entry Name', 'Organism', 'Gene Names (primary)',
              'Kinetics', 'Function [CC]', 'Rhea ID', 'Gene Ontology (biological process)',
              'Source']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=new_fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

print(f'Done! {len(rows)} rows written to {OUTPUT}')
print()
print('Sample:')
print('-' * 80)
for row in rows[:3]:
    print(f"Entry:     {row['Entry']}")
    print(f"Recommended: {row['Recommended name']}")
    print(f"Alt names:   {row['Alternative names']}")
    print(f"EC numbers:  {row['EC numbers']}")
    print('-' * 40)
