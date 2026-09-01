# -*- coding: utf-8 -*-
"""终极验证: build_terpene_pairs 输出 + 孤儿末尾变换 == 旧表 (逐格一致)。"""
import csv

ORPHANS = {'B5A435', 'K9Y6Y9', 'Q45222', 'S0ENM8'}


def load(p):
    with open(p, encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def triples(r):
    t = []
    i = 1
    while r.get('Enzyme_%d' % i, ''):
        t.append((r['Enzyme_%d' % i], r['Rhea ID_%d' % i], r['Direction_%d' % i]))
        i += 1
    return t


def rebuild(rows):
    """每对重建: 酶按字母序, 孤儿末尾。返回 (key, sub, prod, triples)。"""
    out = []
    for r in rows:
        t = triples(r)
        norm = sorted([x for x in t if x[0] not in ORPHANS])
        orph = sorted([x for x in t if x[0] in ORPHANS])
        out.append((r['Substrate ChEBI'], r['Product ChEBI'],
                    r['Substrate'], r['Product'], norm + orph))
    return out


new = load('output_terpene_pairs.tsv')
old = load('../for_graph/uniprotkb_terpene_pairs.tsv')
nb, ob = rebuild(new), rebuild(old)

print('pair order identical:',
      [x[0:2] for x in nb] == [x[0:2] for x in ob])
bad = 0
for a, b in zip(nb, ob):
    if a != b:
        bad += 1
        if bad <= 5:
            print('DIFF:')
            print('  new:', a)
            print('  old:', b)
print('rows differing after orphan-last transform: %d / %d' % (bad, len(nb)))
