"""ETL Step 4: 酶-反应边表 —— A 类(有 source_type, 按来源替换)。

## 三个旧缺陷

1. **edge_id 按位置生成**。原实现 `EDGE{i+1:07d}` 在去重之后按行序编号, 但去重键是
   `(enzyme_id, reaction_id)` —— 中间插入一个新行会让后面所有编号平移, 新 pair 于是拿到
   已被占用的 ID -> PK 冲突。改成与 enzyme_id 同样的「编号持久化」: 已存在的 pair 沿用
   原编号, 新 pair 取 `MAX+1`。重跑幂等且永不复用。

2. **「读现有 -> 过滤掉已存在的」= 已存在就跳过**。已有边的字段变化永远不生效
   (违反「只补充 + 只新增」)。改成 upsert。

3. **`range(1, 46)` 写死列宽** —— 已修: 按 `df.columns` 里实际存在的 `Enzyme_N` 推导。
   实测已提交的 pairs 表 `max_n` 恰好 = 45(零余量), 再加一个酶就会被静默丢掉。

## source_type 从 enzyme 表取, 不从文件名猜

边的来源 = 它那个酶的来源。`enzyme.source_type` 在 step 2 已经写好且权威, 两条路径
(rhea 子表 / pairs 表) 都能统一取到 —— pairs 是汇合表, 本来就没有 per-enzyme 的来源列。
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cell_values import split_multi_value  # noqa: E402
from config import DB_URL  # noqa: E402
from db_utils import upsert_dataframe  # noqa: E402
from sources import read_merged, read_segmented  # noqa: E402

engine = create_engine(DB_URL)

RHEA_FILE = 'for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
PAIRS_FILE = 'for_graph/uniprotkb_terpene_pairs.tsv'


def _get_id_map(table, external_col, internal_col):
    """外部 ID -> 内部 ID 的映射。"""
    df = pd.read_sql(f"SELECT {external_col}, {internal_col} FROM {table}", engine)
    return dict(zip(df[external_col], df[internal_col]))


def _enzyme_attrs():
    """enzyme_id -> (source_type, review_status)。边的来源属性直接照抄它的酶。"""
    df = pd.read_sql("SELECT enzyme_id, source_type, review_status FROM enzyme", engine)
    return {str(r.enzyme_id): (str(r.source_type), str(r.review_status))
            for r in df.itertuples()}


def load_edges_from_rhea(only=None):
    """uniprotkb_rhea.tsv 一行 = 一条酶-反应边。"""
    df = read_segmented(RHEA_FILE, only=only, dtype=str)

    edges = df[["Entry", "Rhea ID"]].dropna(subset=["Rhea ID", "Entry"]).copy()
    edges = edges[(edges["Rhea ID"].astype(str).str.strip() != "")
                  & (edges["Entry"].astype(str).str.strip() != "")]
    edges = edges.drop_duplicates()

    enzyme_map = _get_id_map("enzyme", "uniprot_id", "enzyme_id")
    reaction_map = _get_id_map("reaction", "rhea_id", "reaction_id")

    edges["enzyme_id"] = edges["Entry"].map(enzyme_map)
    edges["reaction_id"] = edges["Rhea ID"].map(reaction_map)
    edges = edges.dropna(subset=["enzyme_id", "reaction_id"])

    return pd.DataFrame({"enzyme_id": edges["enzyme_id"], "reaction_id": edges["reaction_id"]})


def load_edges_from_pairs(only=None):
    """展开宽表 terpene_pairs.tsv: 一个底物-产物对 -> N 个酶。

    pairs 是**汇合表**(无后缀): 同一对化合物下两个来源的酶落在同一行, 所以不能分段。
    only=<s> 时按酶的来源过滤, 免得把另一个来源的边也重算一遍。
    """
    df = read_merged(PAIRS_FILE, dtype=str)

    enzyme_map = _get_id_map("enzyme", "uniprot_id", "enzyme_id")
    reaction_map = _get_id_map("reaction", "rhea_id", "reaction_id")
    attrs = _enzyme_attrs()

    # 列宽按表里实际存在的 Enzyme_N 推导, 不写死上限 ——
    # build_terpene_pairs.py 的 max_n 是跟着数据走的。写死会在酶变多时静默丢行:
    # 已提交的 pairs 表 max_n 恰好 = 45, 零余量。
    max_n = max(
        (int(c[len('Enzyme_'):]) for c in df.columns
         if c.startswith('Enzyme_') and c[len('Enzyme_'):].isdigit()),
        default=0,
    )

    rows = []
    for _, row in df.iterrows():
        for i in range(1, max_n + 1):
            enzyme_col = f"Enzyme_{i}"
            rhea_col = f"Rhea ID_{i}"
            if enzyme_col not in df.columns or rhea_col not in df.columns:
                continue
            entry = str(row.get(enzyme_col) or '').strip()
            if not entry:
                continue
            enz_id = enzyme_map.get(entry)
            if not enz_id:
                continue
            if only and attrs.get(enz_id, ('', ''))[0] != only:
                continue
            # Rhea ID_i 可能是 '; ' 拼接的多值单元格 (见 cell_values.py):
            # 拆开后逐个建边。原来拿整串查 reaction_map 必然 miss, 这些边被静默丢掉。
            for one_rhea in split_multi_value(row.get(rhea_col)):
                rxn_id = reaction_map.get(one_rhea)
                if rxn_id:
                    rows.append({"enzyme_id": enz_id, "reaction_id": rxn_id})

    if not rows:
        return pd.DataFrame(columns=["enzyme_id", "reaction_id"])
    return pd.DataFrame(rows).drop_duplicates()


# purge 会**先删掉本来源的边**(不删就没法删 enzyme, 外键挡着), 编号随之消失。
# 所以 etl_run 在 purge 之前调用 snapshot_edge_ids() 存一份, 让重新插入的边沿用原编号 ——
# 否则每次单来源刷新都会给该来源所有边换号。
_SEED_EDGE_IDS = {}


def snapshot_edge_ids():
    """在 purge 之前取 (enzyme_id, reaction_id) -> edge_id 快照。由 etl_run 调用。"""
    global _SEED_EDGE_IDS
    with engine.connect() as conn:
        ex = pd.read_sql("SELECT edge_id, enzyme_id, reaction_id FROM enzyme_reaction_edge", conn)
    _SEED_EDGE_IDS = {(str(a), str(b)): str(e)
                      for e, a, b in zip(ex.edge_id, ex.enzyme_id, ex.reaction_id)}
    print(f'  edge_id 快照: {len(_SEED_EDGE_IDS)} 条 (purge 前)')
    return _SEED_EDGE_IDS


def _assign_edge_ids(all_edges):
    """已存在的 pair 沿用原编号, 新 pair 取 MAX+1。重跑幂等、编号不复用。

    编号来源 = purge 前的快照 ∪ 当前表。快照覆盖被清的来源, 当前表覆盖另一个来源,
    并集就是刷新前的完整编号情况。
    """
    with engine.connect() as conn:
        ex = pd.read_sql("SELECT edge_id, enzyme_id, reaction_id FROM enzyme_reaction_edge", conn)
    known = dict(_SEED_EDGE_IDS)
    known.update({(str(a), str(b)): str(e)
                  for e, a, b in zip(ex.edge_id, ex.enzyme_id, ex.reaction_id)})
    nxt = 1 + max((int(e[4:]) for e in known.values()
                   if e.startswith('EDGE') and e[4:].isdigit()), default=0)

    ids, reused, allocated = [], 0, 0
    for enz, rxn in zip(all_edges["enzyme_id"], all_edges["reaction_id"]):
        eid = known.get((str(enz), str(rxn)))
        if eid:
            reused += 1
        else:
            eid = f'EDGE{nxt:07d}'
            nxt += 1
            allocated += 1
        ids.append(eid)
    all_edges['edge_id'] = ids
    print(f'  edge_id: 沿用 {reused}, 新分配 {allocated}')
    return all_edges


def load_edges(only=None):
    attrs = _enzyme_attrs()

    parts = [load_edges_from_rhea(only=only), load_edges_from_pairs(only=only)]
    all_edges = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    if all_edges.empty:
        print('  enzyme_reaction_edge: 没有边')
        return
    all_edges = all_edges.drop_duplicates(subset=["enzyme_id", "reaction_id"]).reset_index(drop=True)

    missing = [e for e in all_edges['enzyme_id'] if e not in attrs]
    if missing:
        raise KeyError(f'{len(missing)} 条边的 enzyme_id 不在 enzyme 表里, 例如 {missing[:3]}; '
                       'source_type 无处可取')

    all_edges['source_type'] = [attrs[e][0] for e in all_edges['enzyme_id']]
    all_edges['review_status'] = [attrs[e][1] for e in all_edges['enzyme_id']]
    all_edges = _assign_edge_ids(all_edges)

    cols = ["edge_id", "enzyme_id", "reaction_id", "source_type", "review_status"]
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "enzyme_reaction_edge", all_edges[cols],
                             update_cols=["source_type", "review_status"])
    print(f'  enzyme_reaction_edge: upsert {n} 行 (幂等; 已有边沿用原编号)')
    for s in sorted(set(all_edges['source_type'])):
        print(f'     {s}: {int((all_edges.source_type == s).sum())}')


if __name__ == "__main__":
    load_edges()


def run(only=None):
    load_edges(only=only)
