"""ETL Step 6: 从所有 TSV 来源构建宽表检索索引。

## 这张表同时有 B 类和 C 类的行 —— 必须拆开处理

判据是 `enzyme_id` 是否为空(`enzyme_id` 有 FK 指向 enzyme):

| 行 | 来源 | 处理 |
|---|---|---|
| `enzyme_id IS NULL` | 只由 `_add_compounds` 产生: 化合物**实体本身**的行 | **B 类**: 与来源无关, 由全局文件完整重建 |
| `enzyme_id IS NOT NULL` | 酶/反应/GO/文献/序列链接/isoform/化合物-酶关联 | **C 类**: 按来源替换 |

⚠️ 整表归 C 是**错的**: `WHERE enzyme_id IN (SELECT ... WHERE source_type=<s>)`
永远匹配不上 NULL 行, 于是这些行每次刷新都**再插一份**, 单调增长。
反过来整表 truncate 又破坏来源隔离(刷新 TrEMBL 会把 SwissProt 的索引行清掉)。
所以 `_clear_index()` 分两段清。

**为什么 B 类可以整段清掉重建**: 它们只读 `for_compound_card/...terpene_compounds.tsv`
和 `for_graph/all_nodes.tsv` 这两张**汇合表**(无后缀、与来源无关), 两种模式下读到的东西
完全一样 —— 所以清掉再建既不破坏隔离, 也不会因为「只刷新一个来源」而少数据。
而「值变了要反映出来」要求清: 索引行的身份是 (实体, 字段, 值), 化合物 SMILES 变了
就是换了一行, 旧行不再为真, 留着就是脏数据。

## 列宽不写死

`_add_sequence_links` 原先把宽度写死成 `range(1, 25)` / `range(1, 12)`。
实测现表 `INSDC_Nuc_ID_24` 与 `RefSeq_Prot_ID_11` **各有 1 条真实数据** ——
零余量, 再出现一个 25 段链接的条目就会被静默丢掉。改成像 `Enzyme_N` 一样按列头推导。

## 边生成边写(方案 Phase 3.1)

原来是「把全部行攒进 `list[dict]` -> `pd.DataFrame(rows)` 再复制一份 ->
`drop_duplicates` 建一次全量哈希 -> 一次 `to_sql`」—— 三重峰值。
996 个酶时 13.8 万行还好, 96k 酶下实测每酶 34–90 行(见下), 即 **330 万–870 万行**,
峰值会到 GB 级。现在改为**逐表逐切片**生成、去重、落库, 峰值与总行数无关。

**与原来的行集合等价**, 论证分两步:

1. 去重键 `DEDUPE_COLS` 里含 `source_file`, 而每个逻辑表各对应一个不同的 `source_file`
   —— 所以「跨文件的重复」本来就不算重复(全量去重也不会合并它们),
   逐表去重与全量去重等价;
2. 同一文件内部的重复行, **全部来自单个输入行内部**(同一个值出现在多个列位, 例如
   `Recommended name` 与 `Recommended Name` 两列同值、同一 INSDC 链接在多列重复出现),
   而一个输入行整体落在同一个切片里 —— 所以逐切片去重也等价。

第 2 条不是推断, 是**实测**的: 沙箱里全量去重共去掉 **1,474 / 166,655** 行(0.9%),
按 `source_file` 分布为 rhea 128 / sequence_links 384 / rhea_summary 384 /
terpene_only 350 / pairs 228, 且「按 6 列键去重」与「按全列去重」得到的行数**完全相同**
(都是 165,181)—— 即被去掉的每一行都与另一行**逐列相同**, 没有一例是「键相同但内容不同」。

> ⚠️ **残留**: 若某个源文件里出现了**完全重复的输入行**(源侧异常, 实测未出现),
> 且这两行被切片边界分开, 逐片去重就会漏掉。所以落库后加了一句
> 「重复事实检查」(按去重键 GROUP BY) 把它显式量出来 —— 计数不为 0 就会打印 WARN,
> 而不是静默留下重复行。
"""
import hashlib
import re

import pandas as pd
from sqlalchemy import create_engine, text

from cell_values import split_multi_value
from config import DB_URL
from db_utils import schema_ddl
from sources import (columns_of, indexed_groups, indexed_width, read_merged,  # noqa: F401
                     read_segmented)

engine = create_engine(DB_URL)

# 分段表(11 张): 每个来源一份 for_*/<name>.<source>.tsv, 由 sources.read_segmented 取并集。
TERPENE_ONLY_FILE = "for_graph/uniprotkb_terpene_only.tsv"
RHEA_SUMMARY_FILE = "for_enzyme_reation_card/uniprotkb_rhea_summary.tsv"
ENZYME_MERGED_FILE = "for_enzyme_reation_card/uniprotkb_enzyme_merged.tsv"
MASTER_FILE = "for_enzyme_detail/uniprotkb_master.tsv"
NAMES_FILE = "for_enzyme_detail/child_tables/uniprotkb_names_split.tsv"
RHEA_FILE = "for_enzyme_detail/child_tables/uniprotkb_rhea.tsv"
REFERENCES_FILE = "for_enzyme_detail/child_tables/uniprotkb_references.tsv"
SEQ_LINKS_FILE = "for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv"
GO_FILE = "for_enzyme_detail/child_tables/uniprotkb_go.tsv"
ISOFORM_FILE = "for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv"

# 汇合表(无后缀): 跨条目聚合, 与来源无关 —— 同一对底物-产物天然被两个来源的酶共享。
COMPOUND_FILE = "for_compound_card/uniprotkb_terpene_compounds.tsv"
ALL_NODES_FILE = "for_graph/all_nodes.tsv"
TERPENE_PAIRS_FILE = "for_graph/uniprotkb_terpene_pairs.tsv"

EXCLUDED_COMMON_COMPOUND_IDS = {"CHEBI:15377", "CHEBI:15378", "CHEBI:33019"}

# ---- 分批参数(方案 Phase 3.1) ----
# 按输入行数切片的粒度: 它决定单次「未去重行」的峰值。按实测每输入行产出的行数
# (sequence_links 最宽: 每段链接约 20 行, 实测宽度上限 31 段)取, 2000 行 ≈ 4 万输出行。
INPUT_CHUNK = 2000
# 攒够这么多**去重后**的行就落一次库。10 万行 ≈ 几十 MB, 与总量无关。
FLUSH_ROWS = 100000
# to_sql 每次提交的行数(在 FLUSH_ROWS 之后再切一层, 控制单个 INSERT 的大小)。
INSERT_CHUNK = 10000


def _read_seg(rel_path, only=None, **kwargs):
    return read_segmented(rel_path, only=only, dtype=str, **kwargs)


def _read_merged(rel_path):
    return read_merged(rel_path, dtype=str)


def _clean(value):
    if pd.isna(value):
        return None
    text_value = str(value).strip()
    if not text_value or text_value == "-":
        return None
    return text_value


def _hash(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _add(rows, entity_type, entity_id, enzyme_id, source_file, field_name, value, weight):
    """source_file 传**逻辑表名**(无后缀的 rel path), 不传磁盘上那份分段文件的路径 ——
    否则同一张逻辑表在 SwissProt / TrEMBL 行上会写成两个不同的值。它只作描述用
    (全仓库没有任何查询或 UI 读它), 保持稳定即可。"""
    value = _clean(value)
    entity_id = _clean(entity_id)
    if not value or not entity_id:
        return
    if entity_type == "compound" and entity_id in EXCLUDED_COMMON_COMPOUND_IDS:
        return
    rows.append({
        "entity_type": entity_type,
        "entity_id": entity_id,
        "enzyme_id": _clean(enzyme_id),
        "source_file": source_file,
        "field_name": field_name,
        "field_value": value,
        "field_value_hash": _hash(value.lower()),
        "weight": weight,
    })


def _get_map(table, external_col, internal_col):
    try:
        df = pd.read_sql(f"SELECT {external_col}, {internal_col} FROM {table}", engine)
    except Exception:
        return {}
    return {
        str(row[external_col]).strip(): str(row[internal_col]).strip()
        for _, row in df.iterrows()
        if _clean(row.get(external_col)) and _clean(row.get(internal_col))
    }


def _filter_chebi_ids(value):
    value = _clean(value)
    if not value:
        return None
    ids = [
        chebi_id for chebi_id in re.findall(r"CHEBI:\d+", value)
        if chebi_id not in EXCLUDED_COMMON_COMPOUND_IDS
    ]
    return "; ".join(dict.fromkeys(ids)) if ids else None


# 酶字段表: (源列名, 索引里的 field_name, 权重)。同一 field_name 可以来自不同拼写的列
# (names_split 用 `Recommended name`, master 用 `Recommended Name`)。
ENZYME_FIELDS = [
    ("Entry", "uniprot_id", 95),
    ("UniProt Link", "uniprot_url", 35),
    ("Entry Name", "entry_name", 65),
    ("Organism", "organism", 35),
    ("Recommended name", "primary_name", 65),
    ("Recommended Name", "primary_name", 65),
    ("Protein name", "primary_name", 60),
    ("Alternative names", "alternative_names", 45),
]


def _enzyme_usecols(rel, only=None):
    """本文件里**确实存在**的那些酶字段列 —— 只读这些。

    master 是宽度动态的宽表(实测 TrEMBL 段 473 列), 而它只贡献 3 个字段
    (Entry / UniProt Link / Recommended Name): 全列解析 96k × 473 是 GB 级峰值,
    而这一步要的只是 3 列。缺列不报错 —— 与原来的 `if column in df.columns` 行为一致。

    返回 None 表示「退回全列读取」: 若连 Entry 都不在这些列里, 用 usecols 会把所有行
    当成没有 Entry 而**静默跳过**, 那比慢更糟。
    """
    names_by_source = columns_of(rel, only=only)
    present = [c for c, _, _ in ENZYME_FIELDS
               if all(c in names for names in names_by_source.values())]
    absent = [c for c, _, _ in ENZYME_FIELDS if c not in present]
    if absent:
        print(f'  [usecols] {rel}: 缺 {absent} —— 这几个字段本步不产出')
    if "Entry" not in present:
        print(f'  [usecols] {rel}: 连 Entry 都缺, 退回全列读取')
        return None
    return present


def _add_enzyme_file(rows, df, source_file, entry_to_enzyme_id):
    fields = ENZYME_FIELDS
    for _, row in df.iterrows():
        entry = _clean(row.get("Entry"))
        enzyme_id = entry_to_enzyme_id.get(entry)
        if not enzyme_id:
            continue
        for column, field_name, weight in fields:
            if column in df.columns:
                _add(rows, "enzyme", enzyme_id, enzyme_id, source_file, field_name, row.get(column), weight)


def _compound_to_enzyme_ids(entry_to_enzyme_id):
    """化合物 -> 用它做底物/产物的酶。来源过滤已由 build_rows 收窄映射做掉。"""
    mapping = {}
    terpene_only = _read_seg(TERPENE_ONLY_FILE)
    for _, row in terpene_only.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        for column in ("Substrate ChEBI", "Product ChEBI"):
            compound_id = _clean(row.get(column))
            if compound_id and compound_id not in EXCLUDED_COMMON_COMPOUND_IDS:
                mapping.setdefault(compound_id, set()).add(enzyme_id)

    pairs = _read_merged(TERPENE_PAIRS_FILE)
    for _, row in pairs.iterrows():
        compound_ids = [
            _clean(row.get("Substrate ChEBI")),
            _clean(row.get("Product ChEBI")),
        ]
        compound_ids = [
            compound_id for compound_id in compound_ids
            if compound_id and compound_id not in EXCLUDED_COMMON_COMPOUND_IDS
        ]
        if not compound_ids:
            continue
        for index in indexed_groups(pairs.columns, "Enzyme"):
            enzyme_id = entry_to_enzyme_id.get(_clean(row.get(f"Enzyme_{index}")))
            if not enzyme_id:
                continue
            for compound_id in compound_ids:
                mapping.setdefault(compound_id, set()).add(enzyme_id)

    return mapping


def _add_compounds(rows, df, source_file, compound_to_enzyme_ids):
    fields = [
        ("ChEBI ID", "compound_id", 85),
        ("ChEBI ID", "chebi_id", 85),
        ("Name", "compound_name", 65),
        ("SMILES", "smiles", 30),
        ("Molecular Mass", "average_mass", 20),
        ("ChEBI URL", "chebi_url", 25),
        ("InChI Key", "inchi_key", 50),
    ]
    for _, row in df.iterrows():
        compound_id = _clean(row.get("ChEBI ID"))
        if not compound_id:
            continue
        for column, field_name, weight in fields:
            if column in df.columns:
                # enzyme_id=None: 化合物**实体本身**的行 -> B 类, 与来源无关。
                _add(rows, "compound", compound_id, None, source_file, field_name, row.get(column), weight)
                for enzyme_id in compound_to_enzyme_ids.get(compound_id, set()):
                    _add(rows, "compound", compound_id, enzyme_id, source_file, field_name, row.get(column), weight)


def _add_rhea_rows(rows, df, source_file, entry_to_enzyme_id, rhea_to_reaction_id):
    for _, row in df.iterrows():
        entry = _clean(row.get("Entry"))
        enzyme_id = entry_to_enzyme_id.get(entry)
        rhea_id = _clean(row.get("Rhea ID"))
        reaction_id = rhea_to_reaction_id.get(rhea_id, rhea_id)
        if not enzyme_id:
            continue
        _add(rows, "enzyme", enzyme_id, enzyme_id, source_file, "uniprot_id", entry, 95)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "rhea_id", rhea_id, 90)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "ec_number", row.get("EC Number") or row.get("EC number"), 70)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_equation", row.get("Equation"), 55)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_direction", row.get("Direction"), 25)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_smiles", row.get("Reaction SMILES"), 30)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "chebi_ids", _filter_chebi_ids(row.get("ChEBI IDs (equation order)")), 40)


def _add_terpene_only(rows, df, source_file, entry_to_enzyme_id, rhea_to_reaction_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        reaction_id = rhea_to_reaction_id.get(_clean(row.get("Rhea ID")), _clean(row.get("Rhea ID")))
        if not enzyme_id:
            continue
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "rhea_id", row.get("Rhea ID"), 90)
        _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate_chebi", row.get("Substrate ChEBI"), 80)
        _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate", row.get("Substrate"), 60)
        _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product_chebi", row.get("Product ChEBI"), 80)
        _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product", row.get("Product"), 60)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_direction", row.get("Direction"), 25)


def _add_terpene_pairs(rows, df, source_file, entry_to_enzyme_id, rhea_to_reaction_id):
    """pairs 是汇合表: 同一对化合物下两个来源的酶落在同一行。按来源过滤在 build_rows
    收窄 entry_to_enzyme_id 时已经做掉了, 这里不必再判来源。"""
    for _, row in df.iterrows():
        for index in indexed_groups(df.columns, "Enzyme"):
            enzyme_entry = _clean(row.get(f"Enzyme_{index}"))
            enzyme_id = entry_to_enzyme_id.get(enzyme_entry)
            if not enzyme_id:
                continue
            _add(rows, "enzyme", enzyme_id, enzyme_id, source_file, "uniprot_id", enzyme_entry, 95)
            # 以下 4 条与 Rhea ID 无关, 每酶只出一条 —— 放进下面的循环会按 Rhea 数翻倍。
            _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate_chebi", row.get("Substrate ChEBI"), 80)
            _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate", row.get("Substrate"), 60)
            _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product_chebi", row.get("Product ChEBI"), 80)
            _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product", row.get("Product"), 60)
            # Rhea ID_i 可能是 '; ' 拼接的多值单元格 (见 cell_values.py)。
            # 原来拿整串当 entity_id: 索引里多出一条永远匹配不上的假实体,
            # 而该酶真实催化的那几个反应一条都没进索引。
            for one_rhea in split_multi_value(row.get(f"Rhea ID_{index}")):
                reaction_id = rhea_to_reaction_id.get(one_rhea, one_rhea)
                _add(rows, "reaction", reaction_id, enzyme_id, source_file, "rhea_id", one_rhea, 90)
                _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_direction", row.get(f"Direction_{index}"), 25)


def _add_references(rows, df, source_file, entry_to_enzyme_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        for index in indexed_groups(df.columns, "PMID"):
            ref_entity = _clean(row.get(f"PMID_{index}")) or _clean(row.get(f"DOI_{index}")) or f"{enzyme_id}:reference:{index}"
            for column, field_name, weight in [
                (f"PMID_{index}", "pubmed_id", 80),
                (f"DOI_{index}", "doi", 80),
                (f"Title_{index}", "reference_title", 55),
                (f"Authors_{index}", "reference_authors", 35),
                (f"Journal_{index}", "journal", 35),
                (f"Volume_{index}", "volume", 15),
                (f"Pages_{index}", "pages", 15),
                (f"Year_{index}", "year", 25),
                (f"Type_{index}", "reference_type", 25),
                (f"Positions_{index}", "evidence_positions", 35),
                (f"URL_{index}", "reference_url", 25),
            ]:
                _add(rows, "evidence", ref_entity, enzyme_id, source_file, field_name, row.get(column), weight)


def _add_go(rows, df, source_file, entry_to_enzyme_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        go_id = _clean(row.get("GO ID")) or f"{enzyme_id}:go"
        _add(rows, "go", go_id, enzyme_id, source_file, "go_id", row.get("GO ID"), 75)
        _add(rows, "go", go_id, enzyme_id, source_file, "go_term", row.get("GO Term"), 55)
        _add(rows, "go", go_id, enzyme_id, source_file, "go_url", row.get("GO Link"), 20)


def _add_sequence_links(rows, df, source_file, entry_to_enzyme_id):
    # 宽度按列头推导, 不写死 —— 实测现表 INSDC_Nuc_ID_24 / RefSeq_Prot_ID_11 各有 1 条
    # 真实数据, 即写死的上限已经被顶满, 再多一段链接就被静默丢掉。
    insdc_max = indexed_width(df.columns, "INSDC_Nuc_ID", "INSDC_Prot_ID", "INSDC_Molecule")
    refseq_max = indexed_width(df.columns, "RefSeq_Nuc_ID", "RefSeq_Prot_ID", "RefSeq_Molecule")
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        for index in range(1, insdc_max + 1):
            nuc_id = _clean(row.get(f"INSDC_Nuc_ID_{index}"))
            prot_id = _clean(row.get(f"INSDC_Prot_ID_{index}"))
            molecule = row.get(f"INSDC_Molecule_{index}")
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "accession", nuc_id, 70)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_source", "INSDC nucleotide", 25)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "molecule_type", molecule, 25)
            for link_col in ("EMBL", "GenBank", "DDBJ"):
                _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_url", row.get(f"INSDC_Nuc_{link_col}_Link_{index}"), 15)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "accession", prot_id, 70)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_source", "INSDC protein", 25)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "molecule_type", molecule, 25)
            for link_col in ("EMBL", "GenBank", "DDBJ"):
                _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_url", row.get(f"INSDC_Prot_{link_col}_Link_{index}"), 15)

        for index in range(1, refseq_max + 1):
            prot_id = _clean(row.get(f"RefSeq_Prot_ID_{index}"))
            nuc_id = _clean(row.get(f"RefSeq_Nuc_ID_{index}"))
            molecule = row.get(f"RefSeq_Molecule_{index}")
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "accession", prot_id, 70)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_source", "RefSeq protein", 25)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_url", row.get(f"RefSeq_Prot_Link_{index}"), 15)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "molecule_type", molecule, 25)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "accession", nuc_id, 70)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_source", "RefSeq nucleotide", 25)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_url", row.get(f"RefSeq_Nuc_Link_{index}"), 15)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "molecule_type", molecule, 25)


def _add_isoforms(rows, df, source_file, entry_to_enzyme_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        isoform_id = _clean(row.get("Isoform_ID"))
        if not enzyme_id or not isoform_id:
            continue
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_id", isoform_id, 75)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_length", row.get("Isoform Length"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_mass", row.get("Isoform Mass"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "canonical_length", row.get("Canonical Length"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "canonical_mass", row.get("Canonical Mass"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "canonical_sequence", row.get("Canonical Sequence"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_sequence", row.get("Sequence"), 20)


def _step_sources(entry_to_enzyme_id, compound_to_enzyme_ids, rhea_to_reaction_id, only):
    """[(逻辑表, 是否分段, usecols, 生成函数)] —— **一个逻辑表一步**。

    分步是为了让峰值内存 = 单步的切片行, 而不是全部行。生成函数统一签名 `f(rows, df)`。
    `usecols` 为 None 表示读全列(宽度动态的汇合表与子表本来就窄, 但 master 必须收窄)。
    顺序与原来的 `build_rows` 保持一致(顺序不影响最终行集合, 但保持一致便于比对)。
    """
    steps = []
    for rel in (NAMES_FILE, RHEA_SUMMARY_FILE, ENZYME_MERGED_FILE, MASTER_FILE):
        steps.append((rel, True, _enzyme_usecols(rel, only=only),
                      lambda rows, df, rel=rel: _add_enzyme_file(rows, df, rel, entry_to_enzyme_id)))
    for rel in (COMPOUND_FILE, ALL_NODES_FILE):
        steps.append((rel, False, None,
                      lambda rows, df, rel=rel: _add_compounds(rows, df, rel, compound_to_enzyme_ids)))
    steps += [
        (RHEA_FILE, True, None, lambda rows, df: _add_rhea_rows(
            rows, df, RHEA_FILE, entry_to_enzyme_id, rhea_to_reaction_id)),
        (TERPENE_ONLY_FILE, True, None, lambda rows, df: _add_terpene_only(
            rows, df, TERPENE_ONLY_FILE, entry_to_enzyme_id, rhea_to_reaction_id)),
        (TERPENE_PAIRS_FILE, False, None, lambda rows, df: _add_terpene_pairs(
            rows, df, TERPENE_PAIRS_FILE, entry_to_enzyme_id, rhea_to_reaction_id)),
        (REFERENCES_FILE, True, None, lambda rows, df: _add_references(
            rows, df, REFERENCES_FILE, entry_to_enzyme_id)),
        (GO_FILE, True, None, lambda rows, df: _add_go(rows, df, GO_FILE, entry_to_enzyme_id)),
        (SEQ_LINKS_FILE, True, None, lambda rows, df: _add_sequence_links(
            rows, df, SEQ_LINKS_FILE, entry_to_enzyme_id)),
        (ISOFORM_FILE, True, None, lambda rows, df: _add_isoforms(
            rows, df, ISOFORM_FILE, entry_to_enzyme_id)),
    ]
    return steps


def _iter_batches(only=None):
    """逐表逐切片产出**已去重**的 (逻辑表, DataFrame)。峰值内存与总行数无关。

    only=<s> 时只产出该来源的 C 类行; B 类行(化合物实体)两种模式都一样。

    按来源过滤集中在**一处**: 收窄 `entry_to_enzyme_id`。每个 C 类行都挂在某一个酶上,
    而酶归属唯一一个来源 —— 所以只要这个映射里只剩目标来源的酶, 下面**所有**读取处
    (含 pairs / compounds 这两张无法分段、两个来源混在一行的汇合表)就自动只产出该来源的行。
    逐个读取处判来源会漏 —— 漏的那处就静默多灌一份另一个来源的索引行。
    """
    entry_to_enzyme_id = _get_map("enzyme", "uniprot_id", "enzyme_id")
    if only:
        source_of = _get_map("enzyme", "enzyme_id", "source_type")
        entry_to_enzyme_id = {entry: eid for entry, eid in entry_to_enzyme_id.items()
                              if source_of.get(eid) == only}
    rhea_to_reaction_id = _get_map("reaction", "rhea_id", "reaction_id")
    compound_to_enzyme_ids = _compound_to_enzyme_ids(entry_to_enzyme_id)

    for rel, segmented, usecols, generate in _step_sources(entry_to_enzyme_id, compound_to_enzyme_ids,
                                                          rhea_to_reaction_id, only):
        if segmented:
            df = _read_seg(rel, only=only, **({'usecols': usecols} if usecols else {}))
        else:
            df = _read_merged(rel)
        for start in range(0, len(df), INPUT_CHUNK):
            rows = []
            # 切片按**输入行**切: 一个输入行产出的全部行必然落在同一片里 ——
            # 这正是「逐片去重 == 全量去重」的依据(见模块 docstring)。
            generate(rows, df.iloc[start:start + INPUT_CHUNK])
            if not rows:
                continue
            yield rel, pd.DataFrame(rows).drop_duplicates(subset=DEDUPE_COLS)


def _insert(df):
    with engine.begin() as conn:
        df.to_sql("search_index", conn, if_exists="append", index=False,
                  chunksize=INSERT_CHUNK)


def _count_duplicate_facts():
    """落库后自查: 按去重键分组, 有没有 >1 行的组。

    这是「只补充 + 只新增」在索引上的体现 —— 同一 (实体, 字段, 值) 不该有两行。
    正常输入下必然是 0; 不为 0 就说明**源侧有完全重复的输入行且被切片边界分开了**
    (见模块 docstring 的残留说明), 这时要显式说出来而不是让它留在库里。
    """
    cols = ", ".join(DEDUPE_COLS)
    sql = (f'SELECT COUNT(*) FROM (SELECT 1 FROM search_index '
           f'GROUP BY {cols} HAVING COUNT(*) > 1) AS dup')
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar() or 0)


def _clear_index(only):
    """清掉本次要重建的行。分两段, 都不是全表清空 —— 见模块 docstring。"""
    with engine.begin() as conn:
        conn.execute(text(schema_ddl('search_index')))

        # B 类: 化合物实体行, 与来源无关。只读汇合表, 两种模式下都能完整重建。
        b = conn.execute(text('DELETE FROM search_index WHERE enzyme_id IS NULL')).rowcount

        # C 类: 有 enzyme_id 的行, 按来源清。全量模式下两个来源都清。
        if only:
            c = conn.execute(text('DELETE FROM search_index WHERE enzyme_id IN '
                                  '(SELECT enzyme_id FROM enzyme WHERE source_type = :s)'),
                             {'s': only}).rowcount
        else:
            c = conn.execute(text('DELETE FROM search_index WHERE enzyme_id IS NOT NULL')).rowcount
    print(f'  search_index: 清掉 B 类 {b} 行 (化合物实体) / C 类 {c} 行 (来源={only or "全部"})')


DEDUPE_COLS = ["entity_type", "entity_id", "enzyme_id", "source_file", "field_name", "field_value_hash"]


def load_search_index(only=None):
    _clear_index(only)

    buffer, buffered = [], 0
    n_b_class = n_c_class = 0

    def flush():
        nonlocal buffer, buffered, n_b_class, n_c_class
        if not buffer:
            return
        df = pd.concat(buffer, ignore_index=True) if len(buffer) > 1 else buffer[0]
        n_null = int(df["enzyme_id"].isna().sum())
        n_b_class += n_null
        n_c_class += len(df) - n_null
        _insert(df)
        buffer, buffered = [], 0

    for _, df in _iter_batches(only=only):
        buffer.append(df)
        buffered += len(df)
        if buffered >= FLUSH_ROWS:
            flush()
    flush()

    if n_b_class + n_c_class == 0:
        print("  search_index: no rows to insert")
        return
    print(f"  search_index: {n_b_class + n_c_class} rows inserted "
          f"(B 类 {n_b_class} / C 类 {n_c_class})")

    dup = _count_duplicate_facts()
    if dup:
        print(f"  [WARN] search_index: {dup} 组重复事实 —— 源侧有完全重复的输入行, "
              f"且被切片边界分开(见本模块 docstring 的残留说明)。行内容仍然是对的, "
              f"只是同一事实存了多份。")
    else:
        print("  search_index: 重复事实 0 组 (去重键唯一) ✓")


if __name__ == "__main__":
    run()


def run(only=None):
    load_search_index(only=only)
