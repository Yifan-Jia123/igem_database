"""
验证: UniProt REST API 的数据能否完全涵盖三个导出 (0710/0712/0716) 的所有列。

方法: 全量 1524 条, 批量 POST /uniprotkb/accessions (返回完整条目 JSON),
逐列从 JSON 结构提取与导出 TSV 对比。
结论分类: MATCH(严格相等) / FORMAT(内容一致, 格式不同) / MISMATCH(内容不同) / EMPTY(两边都空)。

用途: 判断工作流能否不依赖手工导出、改为纯 API 拉取 (除 Rhea/ChEBI/PubChem 补充处)。
"""
import csv
import requests
import sys
import time
import re
from collections import Counter

RAW = {
    '0710': '../uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv',
    '0712': '../uniprotkb_TERPENE_AND_reviewed_true_2026_07_12.tsv',
    '0716': '../uniprotkb_terpene_AND_reviewed_true_2026_07_16.tsv',
}
BATCH = 50
MAX_FETCH = None  # 全量; 调试可设小样本


def norm(s):
    """空白折叠 + 统一分隔符, 用于宽松比较。"""
    if s is None:
        return ''
    s = ' '.join(str(s).split())
    s = s.replace(';', '; ').replace(';  ', '; ').strip()
    return s


def texts(c):
    return ' '.join(t.get('text', '') for t in c.get('texts', []))


def walk_ec(d, out):
    if isinstance(d, dict):
        ec = d.get('ecNumber')
        if isinstance(ec, dict) and ec.get('value'):
            out.append(ec['value'])
        for v in d.values():
            walk_ec(v, out)
    elif isinstance(d, list):
        for v in d:
            walk_ec(v, out)


def render_protein_names(pd):
    """把 proteinDescription JSON 渲染成导出列 'Protein names' 的文本格式。"""
    if not pd:
        return ''
    parts = []
    for key, sec in (('recommendedName', ''), ('alternativeName', 'AltName: ')):
        if key in pd:
            for name in (pd[key] if isinstance(pd[key], list) else [pd[key]]):
                full = name.get('fullName', {}).get('value', '')
                short = name.get('shortName', {}).get('value', '')
                ecs = []
                walk_ec(name, ecs)
                s = full
                if ecs:
                    s += ' (' + ', '.join(ecs) + ')'
                if short:
                    s += '; Short: ' + short
                parts.append(sec + s)
    for sec in ('includes', 'contains'):
        if sec in pd:
            for name in pd[sec]:
                full = name.get('fullName', {}).get('value', '')
                ecs = []
                walk_ec(name, ecs)
                s = full + (f' (EC {ecs[0]})' if ecs else '')
                parts.append(f"{sec.capitalize()}: {s}")
    return '; '.join(parts)


def extract(j, col):
    """从完整条目 JSON 提取导出列的值 (尽力还原导出格式)。"""
    if col == 'Entry':
        return j.get('primaryAccession', '')
    if col == 'Entry Name':
        return j.get('uniProtkbId', '')
    if col == 'Protein names':
        return render_protein_names(j.get('proteinDescription'))
    if col == 'Organism':
        return j.get('organism', {}).get('scientificName', '')
    if col == 'Gene Names (primary)':
        return '; '.join(g['geneName']['value'] for g in j.get('genes', []) if 'geneName' in g)
    if col == 'Kinetics':
        parts = []
        for c in j.get('comments', []):
            if c.get('commentType') == 'CATALYTIC ACTIVITY':
                for k in c.get('kinetics', []):
                    for p in k.get('parameters', []):
                        name = p.get('name', '')
                        parts.append(f"{p.get('type')}={p.get('value')} {name}".strip())
                    if k.get('note'):
                        parts.append('Note=' + k['note'])
        return ' '.join(parts)
    if col == 'Function [CC]':
        return ' '.join(texts(c) for c in j.get('comments', []) if c.get('commentType') == 'FUNCTION')
    if col == 'Rhea ID':
        return ' '.join(x['id'] for x in j.get('uniProtKBCrossReferences', []) if x['database'] == 'Rhea')
    if col == 'Gene Ontology (biological process)':
        ids = []
        for x in j.get('uniProtKBCrossReferences', []):
            if x['database'] == 'GO':
                props = {p['key']: p['value'] for p in x.get('properties', [])}
                if props.get('GoTerm', '').startswith('P:'):
                    ids.append(x['id'])
        return '; '.join(ids)
    if col == 'Sequence':
        return j.get('sequence', {}).get('value', '')
    if col == 'EC number':
        ecs = []
        walk_ec(j.get('proteinDescription', {}), ecs)
        return '; '.join(ecs)
    if col == 'Catalytic activity':
        return ' '.join(texts(c) for c in j.get('comments', []) if c.get('commentType') == 'CATALYTIC ACTIVITY')
    if col == 'Alternative products (isoforms)':
        parts = []
        for c in j.get('comments', []):
            if c.get('commentType') == 'ALTERNATIVE PRODUCTS':
                for iso in c.get('isoforms', []):
                    parts.append(' '.join(iso.get('isoformIds', [])))
                for ev in c.get('events', []):
                    s = ev.get('isoformSequenceStatus', '') if isinstance(ev, dict) else ''
                    if s:
                        parts.append(s.replace('_', ' '))
        return ' '.join(parts)
    if col == 'PubMed ID':
        ids = []
        for r in j.get('references', []):
            for x in r.get('citation', {}).get('citationCrossReferences', []):
                if x['database'] == 'PubMed':
                    ids.append(x['id'])
        return '; '.join(dict.fromkeys(ids))
    if col == 'DOI ID':
        ids = []
        for r in j.get('references', []):
            for x in r.get('citation', {}).get('citationCrossReferences', []):
                if x['database'] == 'DOI':
                    ids.append(x['id'])
        return '; '.join(dict.fromkeys(ids))
    if col == 'GeneID':
        return '; '.join(x['id'] for x in j.get('uniProtKBCrossReferences', []) if x['database'] == 'GeneID')
    if col == 'Length':
        return str(j.get('sequence', {}).get('length', ''))
    if col == 'Mass':
        return str(j.get('sequence', {}).get('molWeight', ''))
    if col == 'Alternative sequence':
        # 从 features 提取: "VAR_SEQ 1..323; /note=...; /id=VSP_xxx"
        parts = []
        for f in j.get('features', []):
            if f.get('type') == 'Alternative sequence':
                loc = f.get('location', {})
                st = loc.get('start', {}).get('value', '?')
                en = loc.get('end', {}).get('value', '?')
                note = f.get('description', '')
                fid = f.get('featureId', '')
                s = f'VAR_SEQ {st}..{en}'
                if note:
                    s += f'; /note="{note}"'
                if fid:
                    s += f'; /id="{fid}"'
                parts.append(s)
        return '; '.join(parts)
    return ''


def tokens(s):
    """提取标识符类 token (Rhea/GO/GeneID/PMID/DOI/VSP 等用集合比较)。"""
    return re.findall(r'[A-Z0-9][A-Z0-9_:.\-]*', str(s))


def compare(exp, ap, col):
    if not exp and not ap:
        return 'EMPTY'
    if norm(exp) == norm(ap):
        return 'MATCH'
    # 标识符列: 集合相等即 FORMAT (分隔符差异)
    if col in ('Rhea ID', 'Gene Ontology (biological process)', 'PubMed ID',
               'DOI ID', 'GeneID', 'EC number', 'Alternative sequence'):
        te, ta = set(tokens(exp)), set(tokens(ap))
        if te == ta:
            return 'FORMAT'
    # 文本列: 较长方包含较短方 90% 以上 -> FORMAT
    if len(exp) > 20 and len(ap) > 20:
        e, a = norm(exp), norm(ap)
        if e in a or a in e:
            return 'FORMAT'
        if len(a) > 0 and len(e) > 0:
            import difflib
            r = difflib.SequenceMatcher(None, e, a).ratio()
            if r > 0.9:
                return 'FORMAT'
    return 'MISMATCH'


def main():
    exports = {t: list(csv.DictReader(open(fn, encoding='utf-8'), delimiter='\t')) for t, fn in RAW.items()}
    entries = sorted({r['Entry'] for rows in exports.values() for r in rows})
    if MAX_FETCH:
        entries = entries[:MAX_FETCH]
    print(f'Total entries: {len(entries)}')

    api = {}
    for i in range(0, len(entries), BATCH):
        batch = entries[i:i + BATCH]
        url = f'https://rest.uniprot.org/uniprotkb/accessions?accessions={",".join(batch)}'
        for attempt in range(5):
            try:
                r = requests.post(url, headers={'Accept': 'application/json'}, timeout=180)
                if r.status_code == 200:
                    for e in r.json().get('results', []):
                        api[e['primaryAccession']] = e
                    break
                time.sleep(2 * (attempt + 1))
            except Exception:
                time.sleep(2 * (attempt + 1))
        else:
            print(f'  FAILED batch {i//BATCH+1}', flush=True)
        if i + BATCH < len(entries):
            time.sleep(0.3)
    print(f'Fetched from API: {len(api)}')

    summary = {}
    for tag in ('0710', '0712', '0716'):
        rows = exports[tag]
        cols = [c for c in rows[0].keys() if c != 'Entry']
        for col in cols:
            res = Counter()
            samples = []
            for r in rows:
                acc = r['Entry']
                j = api.get(acc)
                if j is None:
                    res['NO_API'] += 1
                    continue
                kind = compare(r.get(col, ''), extract(j, col), col)
                res[kind] += 1
                if kind == 'MISMATCH' and len(samples) < 2:
                    samples.append((acc, r.get(col, '')[:110], extract(j, col)[:110]))
            summary[(tag, col)] = (res, samples)

    print('\n===== API 覆盖度 =====')
    for (tag, col), (res, samples) in summary.items():
        tot = sum(res.values())
        good = res['MATCH'] + res['FORMAT']
        flag = 'OK ' if (res['MISMATCH'] == 0 and res['NO_API'] == 0) else '!! '
        print(f"{flag}[{tag}] {col:32s} covered {100*good/tot:5.1f}%  "
              f"MATCH={res['MATCH']} FORMAT={res['FORMAT']} MISMATCH={res['MISMATCH']} EMPTY={res['EMPTY']} NO_API={res['NO_API']}")
        for acc, e, a in samples:
            print(f"     e.g. {acc}\n       export: {e}\n       api   : {a}")


if __name__ == '__main__':
    main()
