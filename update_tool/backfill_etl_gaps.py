"""Backfill two columns the ETL used to drop, without a full data reload.

Both are the same kind of gap: the source workbook carries the value, the table
has the column, and the loader simply never wrote it —

  reaction.smiles   0/628   source: uniprotkb_rhea.tsv        "Reaction SMILES"
  gene.gene_name    0/980   source: uniprotkb_master.tsv      "Gene Names"

⚠️ **已被 ETL 修复取代 —— 正常情况下不要再跑这个脚本。**
两个缺口现在都在源头修好了（方案 Phase 2.4 / 2.5）：

  reaction.smiles   `etl_reactions.py` 去掉了「已存在就跳过」，改成 upsert
  gene.gene_name    `load_gene_info` 不再从没有基因名的 accession 文件取，改读 master

保留它只作**一次性修补**用（比如只想补这两列、不想整段重灌）。
它的输入路径已跟着分段改造走 `sources.read_segmented` —— 直接读无后缀的旧文件
会把**陈旧值**写进库（那些文件分段后就不再更新了），所以必须读分段。

Idempotent: re-running only rewrites the same values.

A handful of Rhea ids carry more than one SMILES across their rows; the first
occurrence wins, which is the same rule `drop_duplicates(subset="Rhea ID")`
applies in the ETL.

Run from the repo root, with IGEM_DB_PASSWORD set (as for the ETL):
    python update_tool/backfill_etl_gaps.py
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "etl"))
from config import DB_URL  # noqa: E402
from sources import read_segmented, safe_usecols  # noqa: E402

RHEA_FILE = "for_enzyme_detail/child_tables/uniprotkb_rhea.tsv"
MASTER_FILE = "for_enzyme_detail/uniprotkb_master.tsv"

engine = create_engine(DB_URL)


def _non_empty(value):
    return value if isinstance(value, str) and value.strip() else None


def backfill_reaction_smiles(conn):
    df = read_segmented(RHEA_FILE)
    source = df[["Rhea ID", "Reaction SMILES"]].dropna(subset=["Rhea ID"])
    smiles_by_id = {}
    for rhea_id, smiles in zip(source["Rhea ID"], source["Reaction SMILES"]):
        # keep="first" equivalent — never overwrite an id we already resolved.
        if rhea_id not in smiles_by_id:
            smiles_by_id[rhea_id] = _non_empty(smiles)

    existing = {row[0] for row in conn.execute(text("SELECT reaction_id FROM reaction"))}
    updates = [
        {"rid": rid, "smiles": smiles}
        for rid, smiles in smiles_by_id.items()
        if smiles and rid in existing
    ]
    if updates:
        conn.execute(
            text("UPDATE reaction SET smiles = :smiles WHERE reaction_id = :rid"),
            updates,
        )

    filled = conn.execute(
        text("SELECT COUNT(*) FROM reaction WHERE smiles IS NOT NULL AND smiles <> ''")
    ).scalar()
    total = conn.execute(text("SELECT COUNT(*) FROM reaction")).scalar()
    print(f"  reaction.smiles : {filled}/{total} populated ({len(updates)} written)")
    missing = sorted(existing - {rid for rid, s in smiles_by_id.items() if s})
    if missing:
        print(f"    no SMILES in source for: {missing[:5]}")


def backfill_gene_names(conn):
    # TrEMBL 段与 SwissProt 段的 master 列宽不同, 缺列时退回全列读取(同 etl_master)。
    cols = safe_usecols(MASTER_FILE, ["Entry", "Gene Names"])
    names_df = read_segmented(MASTER_FILE, dtype=str,
                              **({"usecols": cols} if cols else {}))
    by_entry = {}
    for entry, names in zip(names_df["Entry"], names_df["Gene Names"]):
        name = _non_empty(names)
        if entry and name:
            by_entry[entry] = name

    pairs = list(conn.execute(text("SELECT enzyme_id, uniprot_id FROM enzyme")))
    updates = [
        {"eid": enzyme_id, "name": by_entry[uniprot_id]}
        for enzyme_id, uniprot_id in pairs
        if by_entry.get(uniprot_id)
    ]
    if updates:
        conn.execute(
            text("UPDATE gene SET gene_name = :name WHERE enzyme_id = :eid"),
            updates,
        )

    filled = conn.execute(
        text("SELECT COUNT(*) FROM gene WHERE gene_name IS NOT NULL AND gene_name <> ''")
    ).scalar()
    total = conn.execute(text("SELECT COUNT(*) FROM gene")).scalar()
    print(f"  gene.gene_name  : {filled}/{total} populated ({len(updates)} written)")


def main():
    with engine.begin() as conn:
        backfill_reaction_smiles(conn)
        backfill_gene_names(conn)


if __name__ == "__main__":
    main()
