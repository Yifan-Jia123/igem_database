"""ETL Step 1: 化合物表 —— B 类(全局字典, 无来源概念)。

化合物是客观化学实体: 同一个 ChEBI ID 就是同一个化合物, 与它被哪个来源的酶引用无关。
所以**不按来源分段、也不按来源删除**, 只做 upsert(只增 + 原地更新)。

原实现的缺陷: 无查重直接 `append` -> 二次运行主键冲突, ETL 第 1 步就 `sys.exit(1)`。
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL  # noqa: E402
from db_utils import upsert_dataframe  # noqa: E402
from sources import read_merged  # noqa: E402

engine = create_engine(DB_URL)

COMPOUNDS_FILE = 'for_compound_card/uniprotkb_terpene_compounds.tsv'
ALL_NODES_FILE = 'for_graph/all_nodes.tsv'

EXCLUDED_COMMON_COMPOUND_IDS = {"CHEBI:15377", "CHEBI:15378", "CHEBI:33019"}

COMPOUND_COLUMNS = {
    "inchi_key": "VARCHAR(100)",
}


def _fill_blank_name(out):
    """name 为空的化合物用 compound_id 兜底。

    compound.name 是 NOT NULL, 而 all_nodes 里会混进解析不出名字的条目 ——
    全量 TrEMBL 实测有 4 个 POLYMER:* (Rhea 方程里的 ChEBI 聚合物实体,
    PubChem/ChEBI 都查不到名字与 InChI Key)。空名会让**整条 upsert 事务**
    因 (1048, "Column 'name' cannot be null") 回滚, 连非空的那些行一起丢。

    兜底值取 compound_id 而不是空串: 前端 compound_filters.py 的既有约定是
    `Compound.name != Compound.compound_id` 才 displayable, 所以 name=id 恰好
    表达「这是个没有真名的实体」-> 不进图(画出来也会是个没标签的节点),
    但仍然留在字典里, 不影响 reaction_compound 的外键。

    空串与 None 都要认(两者都会在 COALESCE 里被当"没值"或被 MySQL 拒)。
    """
    blank = out["name"].isna() | (out["name"].astype(str).str.strip() == "")
    out.loc[blank, "name"] = out.loc[blank, "compound_id"]
    return out


def _ensure_columns(table_name, columns):
    with engine.connect() as conn:
        for column_name, ddl in columns.items():
            exists = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = :table_name AND column_name = :column_name"
                ),
                {"table_name": table_name, "column_name": column_name},
            ).scalar()
            if not exists:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
        conn.commit()


def load_compounds():
    _ensure_columns("compound", COMPOUND_COLUMNS)
    df = read_merged(COMPOUNDS_FILE)
    df = df[~df["ChEBI ID"].isin(EXCLUDED_COMMON_COMPOUND_IDS)]

    out = pd.DataFrame()
    out["compound_id"] = df["ChEBI ID"]
    out["name"] = df["Name"]
    out["chebi_id"] = df["ChEBI ID"]
    out["smiles"] = df["SMILES"]
    out["average_mass"] = pd.to_numeric(df["Molecular Mass"], errors="coerce")
    out["chebi_url"] = df["ChEBI URL"]
    out["inchi_key"] = None          # 由 supplement_from_all_nodes 填
    out["structure_image_url"] = df["ChEBI ID"].apply(
        lambda x: f"https://www.ebi.ac.uk/chebi/displayImage.do?defaultImage=true&chebiId={x.split(':')[-1]}"
    )
    _fill_blank_name(out)

    cols = ["compound_id", "name", "chebi_id", "smiles", "average_mass",
            "chebi_url", "inchi_key", "structure_image_url"]
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "compound", out[cols],
                             update_cols=[c for c in cols if c != "compound_id"],
                             preserve=("inchi_key",))
    print(f"  compound: upsert {n} 行 (inchi_key 传 NULL 不覆盖已有值)")


def supplement_from_all_nodes():
    """all_nodes.tsv 里的化合物补进 compound 表, 并把 InChI Key 填上。"""
    _ensure_columns("compound", COMPOUND_COLUMNS)
    df = read_merged(ALL_NODES_FILE)
    df = df[~df["ChEBI ID"].isin(EXCLUDED_COMMON_COMPOUND_IDS)]

    has_inchi_key = "InChI Key" in df.columns
    out = pd.DataFrame()
    out["compound_id"] = df["ChEBI ID"]
    out["name"] = df["Name"]
    out["chebi_id"] = df["ChEBI ID"]
    out["inchi_key"] = df["InChI Key"].fillna("") if has_inchi_key else None
    # 空串会让 COALESCE 认为「有值」而把已有 inchi_key 抹成空串 -> 先归一成 None。
    if has_inchi_key:
        out.loc[out["inchi_key"].astype(str).str.strip() == "", "inchi_key"] = None
    _fill_blank_name(out)

    with engine.begin() as conn:
        n = upsert_dataframe(conn, "compound", out,
                             update_cols=["name", "chebi_id", "inchi_key"],
                             preserve=("name", "chebi_id", "inchi_key"))
    print(f"  compound (from all_nodes): upsert {n} 行")


if __name__ == "__main__":
    load_compounds()
    supplement_from_all_nodes()


def run(only=None):
    """only 未使用 —— 化合物与来源无关(B 类)。保留参数是为了步骤签名一致。"""
    load_compounds()
    supplement_from_all_nodes()
