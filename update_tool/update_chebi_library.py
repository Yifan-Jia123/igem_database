"""
更新 ChEBI 参考库 (chebi_full.tsv + chebi_smiles.tsv)。

隔离原则: 所有输出写到本文件夹的 chebi_data/ (默认), 绝不碰 ../chebi_data/ 原始目录。
换新数据时: 用新的 chebi_full.tsv / chebi_smiles.tsv 替换工作流输入路径即可 (build_terpene_compounds,
            build_all_nodes 用 chebi_full; fetch_rhea 用 chebi_smiles)。

数据来源: ChEBI FTP flat files
  compounds.tsv.gz          : id, name, ..., chebi_accession, ...   (Name + 全量化合物清单)
  chemical_data.tsv.gz      : id, compound_id, ..., mass, ..., structure_id  (分子量 + 结构关联)
  structure_registry.tsv.gz : id, structure_id, layers(json), ...   (CANONICAL_SMILES)

输出格式 (与旧库一致):
  chebi_full.tsv   : ChEBI ID, Name, SMILES, Molecular Mass, ChEBI URL   (全部化合物, 按 ChEBI ID 排序)
  chebi_smiles.tsv : CHEBI, SMILES                                        (仅含 SMILES 的化合物)

用法:
  python update_chebi_library.py [输出目录] [--refresh] [--old=<旧库路径>]
    --refresh        强制重新下载 flat 文件 (默认已存在则跳过)
    --old=<旧库路径> 覆盖回填源 (只读)。新库缺失的 SMILES/Mass 用旧库补回,
                     并写出 curation_overrides.tsv 记录补丁。默认 ../chebi_data/chebi_full.tsv
"""
import csv
import gzip
import json
import os
import re
import sys
import urllib.request


def clean_name(row):
    """优先 ascii_name (无 HTML、希腊字母/减号已转 ASCII, 与旧库格式一致);
       空则回退 name 去掉 HTML 标签。"""
    a = row.get('ascii_name', '').strip()
    if a:
        return a
    return re.sub(r'<[^>]+>', '', row.get('name', '')).strip()

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else 'chebi_data'
REFRESH = '--refresh' in sys.argv
# 覆盖回填源: 新库缺失的 SMILES/Mass 从旧参考库补回 (只读, 不修改原始文件)
OLD_LIB = '../chebi_data/chebi_full.tsv'
for a in sys.argv:
    if a.startswith('--old='):
        OLD_LIB = a[len('--old='):]
        break

BASE = 'https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files'
FILES = ['compounds.tsv.gz', 'chemical_data.tsv.gz', 'structure_registry.tsv.gz']

os.makedirs(OUT_DIR, exist_ok=True)


def download():
    """下载 flat 文件到本地 (已存在且非 --refresh 则跳过)。"""
    for fname in FILES:
        path = os.path.join(OUT_DIR, fname)
        if os.path.exists(path) and not REFRESH:
            print(f'{fname}: exists, skip')
            continue
        url = f'{BASE}/{fname}'
        print(f'Downloading {fname} ...', flush=True)
        urllib.request.urlretrieve(url, path)
        print(f'  {os.path.getsize(path)/1024/1024:.1f} MB', flush=True)


def read_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            yield row


def build():
    # 1) compound_id -> (chebi_accession, name)
    acc2name = {}   # chebi_accession -> name
    acc2id = {}     # chebi_accession -> int compound id
    print('Parsing compounds.tsv.gz ...', flush=True)
    for row in read_gz(os.path.join(OUT_DIR, 'compounds.tsv.gz')):
        acc = row['chebi_accession']
        cid = int(row['id'])
        acc2id[acc] = cid
        if acc:
            acc2name[acc] = clean_name(row)

    # 2) chemical_data: compound_id -> mass ; compound_id -> structure_id
    id2mass = {}
    id2struct = {}
    print('Parsing chemical_data.tsv.gz ...', flush=True)
    for row in read_gz(os.path.join(OUT_DIR, 'chemical_data.tsv.gz')):
        cid = int(row['compound_id'])
        if row['mass']:
            id2mass[cid] = row['mass']
        if row['structure_id']:
            id2struct[cid] = int(row['structure_id'])

    # 3) structure_id -> SMILES
    struct2smi = {}
    print('Parsing structure_registry.tsv.gz ...', flush=True)
    for row in read_gz(os.path.join(OUT_DIR, 'structure_registry.tsv.gz')):
        sid = int(row['structure_id'])
        if sid in struct2smi:
            continue
        try:
            layers = json.loads(row['layers'])
            smi = layers.get('CANONICAL_SMILES', '')
            if smi:
                struct2smi[sid] = smi
        except Exception:
            pass

    # 4) 组装
    chebi_full = {}   # accession -> row
    for acc in acc2name:
        cid = acc2id.get(acc)
        smi = struct2smi.get(id2struct.get(cid)) if cid and cid in id2struct else ''
        mass = id2mass.get(cid, '') if cid else ''
        chebi_full[acc] = {
            'ChEBI ID': acc,
            'Name': acc2name[acc],
            'SMILES': smi,
            'Molecular Mass': mass,
            'ChEBI URL': f'https://www.ebi.ac.uk/chebi/{acc}',
        }
    # 若无化合物清单, 退化为按 structure 补全
    if not chebi_full:
        print('WARN: compounds.tsv empty')
        return

    # 4.5) 覆盖回填: 新库缺失而旧库有的 SMILES/Mass 用旧值补回 (人工覆盖补丁)
    overrides = []
    if OLD_LIB and os.path.exists(OLD_LIB):
        old = {}
        with open(OLD_LIB, encoding='utf-8') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                old[row['ChEBI ID']] = row
        for acc, row in chebi_full.items():
            o = old.get(acc)
            if not o:
                continue
            for field in ('SMILES', 'Molecular Mass'):
                if not row[field] and o.get(field):
                    overrides.append((acc, field, o[field]))
                    row[field] = o[field]
        if overrides:
            print(f'\nOverrides backfilled from {OLD_LIB}: {len(overrides)} cells')
            for acc, field, val in overrides:
                print(f'  {acc} {field}: {val[:80]}')
            with open(os.path.join(OUT_DIR, 'curation_overrides.tsv'), 'w',
                      encoding='utf-8', newline='') as f:
                f.write('ChEBI ID\tField\tValue\n')
                for acc, field, val in overrides:
                    f.write(f'{acc}\t{field}\t{val}\n')
            print(f'  Patch log -> {OUT_DIR}/curation_overrides.tsv')
        else:
            print('\nNo overrides needed (新库与旧库缺失面无重叠)')
    else:
        print(f'\nWARN: old library not found at {OLD_LIB}, no backfill')

    # 5) 写出 chebi_full.tsv (全部化合物, 按 ChEBI ID 排序)
    rows = sorted(chebi_full.values(), key=lambda r: r['ChEBI ID'])
    out = os.path.join(OUT_DIR, 'chebi_full.tsv')
    fields = ['ChEBI ID', 'Name', 'SMILES', 'Molecular Mass', 'ChEBI URL']
    with open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f'chebi_full.tsv: {len(rows)} rows -> {out}')

    # 6) 写出 chebi_smiles.tsv (仅含 SMILES)
    smi_rows = sorted(((r['ChEBI ID'], r['SMILES']) for r in rows if r['SMILES']), key=lambda x: x[0])
    out2 = os.path.join(OUT_DIR, 'chebi_smiles.tsv')
    with open(out2, 'w', encoding='utf-8', newline='') as f:
        f.write('CHEBI\tSMILES\n')
        for acc, smi in smi_rows:
            f.write(f'{acc}\t{smi}\n')
    print(f'chebi_smiles.tsv: {len(smi_rows)} rows -> {out2}')


if __name__ == '__main__':
    download()
    build()
    print('\n完成! 新参考库在', os.path.abspath(OUT_DIR))
