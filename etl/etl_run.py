"""ETL Runner —— 按来源分段重灌。

## 两种模式

    python etl_run.py                   全量: 读磁盘上全部分段, 逐个来源替换
    python etl_run.py --source=trembl   只替换该来源的行, 另一个来源原样不动

## 为什么必须先 purge 再 load

`enzyme` 的子表(gene / gene_sequence_link / evidence / enzyme_go / enzyme_isoform /
enzyme_reaction_edge / search_index)**都没有 `ON DELETE CASCADE`**。所以:

  1. 删除顺序必须是「子表 -> enzyme」, 否则外键报错
  2. 子表清空必须在**任何插入之前**完成 —— 各步骤模块自己做不到这件事
     (etl_enzymes 在 step 2, 但 gene/evidence/... 要到 step 5 才写)

所以「先按来源清干净」集中在本文件做。顺序错了会立刻外键报错, 不是静默错误。

## 幂等性 (方案 Phase 5 验收 A)

purge 把该来源的所有行清成确定状态, 再整段重灌 —— 所以「输入不变 -> 重跑 -> 结果不变」
是结构上成立的。中途失败留下的半成品, 重跑一次即可修复。

⚠️ **purge 与 load 目前不是同一个事务**(各步骤模块自己开事务)。要做到单事务需要把
connection 穿透进 6 个模块的所有读写点。当前设计下崩溃不会造成**不可修复**的状态
(因为 purge 是确定性的), 所以按「可重跑修复」处理, 单事务留作后续加固。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text  # noqa: E402

import etl_compounds  # noqa: E402
import etl_edges  # noqa: E402
import etl_enzymes  # noqa: E402
import etl_master  # noqa: E402
import etl_reactions  # noqa: E402
import etl_search_index  # noqa: E402
from config import DB_URL  # noqa: E402
from sources import SOURCES  # noqa: E402

engine = create_engine(DB_URL)

# 有 enzyme_id 外键、自身没有 source_type 的子表。删除时靠子查询定位来源。
# 顺序无所谓(它们互不引用), 但都必须早于 enzyme 的删除。
CHILD_TABLES = ('enzyme_isoform', 'enzyme_go', 'evidence', 'gene_sequence_link', 'gene')


def purge_source(source):
    """清掉该来源的全部行, 子表优先。编号映射表(enzyme_id_map / enzyme_alias_map)**不在其中**。"""
    ids = 'SELECT enzyme_id FROM enzyme WHERE source_type = :s'
    with engine.begin() as conn:
        # search_index: 只清「有 enzyme_id」的行。enzyme_id IS NULL 的是化合物/实体级行,
        # 与来源无关(同一个化合物就是同一个化合物), 归 B 类由 etl_search_index 自己 upsert。
        # 照搬 C 类的写法会让这些行永远匹配不上 -> 每次刷新重复累积一份。
        n = conn.execute(text(f'DELETE FROM search_index WHERE enzyme_id IN ({ids})'),
                         {'s': source}).rowcount
        print(f'  search_index(有 enzyme_id): -{n}')

        for table in CHILD_TABLES:
            n = conn.execute(text(f'DELETE FROM {table} WHERE enzyme_id IN ({ids})'),
                             {'s': source}).rowcount
            print(f'  {table}: -{n}')

        n = conn.execute(text('DELETE FROM enzyme_reaction_edge WHERE source_type = :s'),
                         {'s': source}).rowcount
        print(f'  enzyme_reaction_edge: -{n}')

        n = conn.execute(text('DELETE FROM enzyme WHERE source_type = :s'),
                         {'s': source}).rowcount
        print(f'  enzyme: -{n}')


def steps_for(only):
    """(标签, 可调用) —— 全部接受 only=<source|None>。"""
    return [
        ('1/6 compounds', lambda o: etl_compounds.run(o)),
        ('2/6 enzymes', lambda o: etl_enzymes.run(o)),
        ('3/6 reactions', lambda o: etl_reactions.run(o)),
        ('4/6 edges', lambda o: etl_edges.run(o)),
        ('5/6 master (sequence + gene + evidence + GO + isoforms)', lambda o: etl_master.run(o)),
        ('6/6 search index', lambda o: etl_search_index.run(o)),
    ]


def parse_args(argv):
    only = None
    for a in argv:
        if a.startswith('--source='):
            only = a[len('--source='):].strip()
            if only not in SOURCES:
                sys.exit(f'未登记的来源 {only!r}; 已登记: {list(SOURCES)}')
        elif a in ('-h', '--help'):
            sys.exit(__doc__.strip())
        else:
            sys.exit(f'未知参数 {a!r}\n\n{__doc__.strip()}')
    return only


def main(argv=None):
    only = parse_args(argv if argv is not None else sys.argv[1:])
    targets = [only] if only else list(SOURCES)

    mode = f'只重灌 {only}' if only else f'全量重灌 ({len(SOURCES)} 个来源)'
    print(f'=== ETL 开始: {mode} ===')

    # 必须在 purge 之前 —— purge 删掉本来源的边后, edge_id 就无从沿用了。
    print('\n--- 快照 edge_id ---')
    etl_edges.snapshot_edge_ids()

    for source in targets:
        print(f'\n--- purge {source} ---')
        purge_source(source)

    for label, fn in steps_for(only):
        print(f'\n[{label}]')
        try:
            fn(only)
            print(f'[{label}] OK')
        except Exception as e:
            print(f'[{label}] FAILED: {type(e).__name__}: {e}')
            print('\n已 purge 的来源处于「已清空但未重灌完」状态 —— 修好后重跑本命令即可 '
                  '(purge 是确定性的, 重跑不会叠加)。')
            sys.exit(1)

    print('\n=== ETL complete ===')


if __name__ == '__main__':
    main()
