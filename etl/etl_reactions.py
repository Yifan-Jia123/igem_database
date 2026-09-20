"""ETL Step 3: 反应表 + reaction_compound —— B 类(全局字典)。

## 为什么 reaction 不按来源删

`reaction` 表**有** source_type 列, 但它不是 A 类。同一个 Rhea 反应会被两个来源共享,
按来源 DELETE 会删掉另一个来源正在引用的反应行, 而
`enzyme_reaction_edge.fk_edge_reaction` 会直接报外键错。

反应是**客观化学实体**, 不是某条 UniProt 记录的属性 —— 所以统一记为 `swiss_prot`
并只增不删, 来源归属交给 `enzyme` / `enzyme_reaction_edge` 表达。

## 顺带修掉的两个旧缺陷

1. `drop_duplicates(subset="Rhea ID")` 全局归并保留 —— 对 reaction 是**对的**
   (一个 Rhea ID 就是一个反应), 保留。
2. 原实现「读现有 -> 过滤掉已存在的」= **已存在就跳过**: 已有反应更新的 `smiles` / `equation`
   永远不会生效。改成 upsert。

## ⚠️ 2026-09-19: `--source=<s>` 模式下 reaction 只新增、不更新

上面那条「改成 upsert」对**全量模式**成立, 但对**单来源模式是错的** —— 实测出来的:

`read_segmented(RHEA_FILE, only=only)` 在单来源模式下只读到那一段, 而下面的
`drop_duplicates(subset='Rhea ID')` 取的是**该段文件里的第一行**。同一个 Rhea ID 的
`Direction` 在两条来源里可以不同(TrEMBL 的自动注释大量是 `not specified`), 于是
「一个 Rhea ID 一个反应」这个跨来源的客观实体, 它的属性变成了**由最后跑的模式决定**:

| RHEA:31007 的 Direction | 读到的第一行 | 映射后 | 图的后果 |
|---|---|---|---|
| 合并视图(swiss_prot 在前) | `left-to-right` | `forward` | 只画底物→产物 |
| 只读 trembl 段 | `not specified` | `unknown` | **双向都画**(`DIRECTION_ALLOWS_*` 都含 unknown) |

实测只跑一次 `--source=trembl`: `reaction` 里 12 行的 `direction` 从 `forward`/`reverse`
翻成 `unknown`(恰好等于「两段 Direction 不一致的共享反应」数, 39 个共享反应里有 12 个)。
行数一个没变, 但首页图从 **157 组 / 7,166.6 KB 变成 169 组 / 8,774.7 KB**, 并多出
`(X,Y)` 与 `(Y,X)` 成对的镜像边 —— 即一次「另一个来源」的刷新改掉了图上画什么。

所以单来源模式下这里是**纯只新增**(`update_cols=[]`, 走 `INSERT IGNORE`):
判断一个共享反应该取哪一行, 只有合并视图做得到; 单来源看半张表, 无权覆盖。
这与同模块 `load_reaction_compounds` 的「已存在则忽略」是同一个道理, 也是隔离原则
(「两个来源互不覆盖」)在共享实体上的落地 —— 更新共享反应的属性由**全量模式**负责
(全量模式读全部分段, 且 `SOURCES` 顺序让 swiss_prot 在前, 结果与修复前一致)。
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL, DIRECTION_MAP  # noqa: E402
from db_utils import upsert_dataframe  # noqa: E402
from sources import read_merged, read_segmented  # noqa: E402

engine = create_engine(DB_URL)

RHEA_FILE = 'for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
TERPENE_COMPOUNDS_FILE = 'for_compound_card/uniprotkb_terpene_compounds.tsv'
GRAPH_NODES_FILE = 'for_graph/all_nodes.tsv'
EXCLUDED_COMMON_COMPOUND_IDS = {"CHEBI:15377", "CHEBI:15378", "CHEBI:33019"}

REACTION_COLS = ["reaction_id", "rhea_id", "equation", "direction", "ec_number",
                 "smiles", "rhea_url", "source_type", "review_status"]


def parse_chebi_ids(chebi_str):
    """'CHEBI:138232 | CHEBI:138233; CHEBI:33019' -> (substrates, products)

    '|' 左边是底物(';' 分隔), 右边是产物。
    """
    if pd.isna(chebi_str) or not str(chebi_str).strip():
        return [], []

    parts = str(chebi_str).split("|")
    substrates = [x.strip() for x in parts[0].split(";") if x.strip()] if len(parts) >= 1 else []
    products = [x.strip() for x in parts[1].split(";") if x.strip()] if len(parts) >= 2 else []
    return substrates, products


def load_allowed_compound_ids():
    """被允许作为图节点的化合物(人工策展过的集合)。"""
    allowed = set()
    for rel in (TERPENE_COMPOUNDS_FILE, GRAPH_NODES_FILE):
        df = read_merged(rel, usecols=["ChEBI ID"])
        allowed.update(
            str(cid).strip() for cid in df["ChEBI ID"].dropna() if str(cid).strip()
        )
    return allowed - EXCLUDED_COMMON_COMPOUND_IDS


def load_reactions(only=None):
    df = read_segmented(RHEA_FILE, only=only, dtype=str)

    # 一个 Rhea ID 就是一个反应: 全局按 Rhea ID 归并(跨来源合并, 与来源无关)。
    # 个别 Rhea ID 有多条 SMILES, 取第一条以保持确定性。
    reactions = df[["Rhea ID", "EC Number", "Equation", "Direction",
                    "Reaction SMILES"]].drop_duplicates(subset="Rhea ID")

    out = pd.DataFrame()
    out["reaction_id"] = reactions["Rhea ID"]
    out["rhea_id"] = reactions["Rhea ID"]
    out["equation"] = reactions["Equation"]
    out["direction"] = reactions["Direction"].map(DIRECTION_MAP).fillna("unknown")
    out["ec_number"] = reactions["EC Number"]
    out["smiles"] = reactions["Reaction SMILES"]
    out["rhea_url"] = reactions["Rhea ID"].apply(
        lambda x: f"https://www.rhea-db.org/rhea/{str(x).split(':')[-1]}"
    )
    out = out[out["rhea_id"].notna() & (out["rhea_id"].astype(str).str.strip() != "")]

    # 反应统一记 swiss_prot: 它是共享的客观实体, 按来源标记会在另一来源引用它时
    # 被按来源 DELETE 删掉 -> 外键错。来源归属由 enzyme / edge 表达。
    out["source_type"] = "swiss_prot"
    out["review_status"] = "official"

    # 单来源模式: 只新增, 不覆盖(理由见模块 docstring)。全量模式: 正常 upsert。
    upd = [] if only else [c for c in REACTION_COLS if c != "reaction_id"]
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "reaction", out[REACTION_COLS].drop_duplicates(subset="reaction_id"),
                             update_cols=upd)
    if only:
        # 措辞用「输入」不用「新增」: upsert_dataframe 返回的是输入行数, 不是写入行数
        # (见 db_utils 的说明)。本模式下这 112 行**全部已存在**、实际新增 0,
        # 若写成「新增 112 行」会让幂等重跑看起来像在重复灌数据。
        print(f"  reaction: 只新增模式, 输入 {n} 行 (冲突即忽略; "
              f"--source={only} 不覆盖共享反应, 属性更新由全量模式负责)")
    else:
        print(f"  reaction: upsert {n} 行")


def load_reaction_compounds(only=None):
    df = read_segmented(RHEA_FILE, only=only, dtype=str)
    allowed_compound_ids = load_allowed_compound_ids()

    rows = []
    skipped_compounds = set()
    for _, row in df.iterrows():
        rhea_id = row["Rhea ID"]
        if pd.isna(rhea_id) or not str(rhea_id).strip():
            continue

        substrates, products = parse_chebi_ids(row.get("ChEBI IDs (equation order)", ""))
        for chebi in substrates:
            if chebi in allowed_compound_ids:
                rows.append({"reaction_id": rhea_id, "compound_id": chebi, "role": "substrate"})
            else:
                skipped_compounds.add(chebi)
        for chebi in products:
            if chebi in allowed_compound_ids:
                rows.append({"reaction_id": rhea_id, "compound_id": chebi, "role": "product"})
            else:
                skipped_compounds.add(chebi)

    if not rows:
        print("  reaction_compound: no rows to insert")
        return

    rc = pd.DataFrame(rows).drop_duplicates()
    # UNIQUE (reaction_id, compound_id, role) 保证幂等: 冲突即忽略。
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "reaction_compound", rc, update_cols=[])
    print(f"  reaction_compound: upsert {n} 行 (已存在则忽略)")
    if skipped_compounds:
        print(f"  reaction_compound: skipped {len(skipped_compounds)} non-curated ChEBI IDs")


if __name__ == "__main__":
    load_reactions()
    load_reaction_compounds()


def run(only=None):
    load_reactions(only=only)
    load_reaction_compounds(only=only)
