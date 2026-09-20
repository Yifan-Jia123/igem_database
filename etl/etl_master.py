"""ETL Step 5: 酶序列回填 + 子表(gene / sequence_link / evidence / go / isoform)。

## 按来源替换 (方案 Phase 2.1 C 类)

这 5 张子表都**有 `enzyme_id` 外键、但没有 `source_type` 列**。它们每个 enzyme_id
都确定性地属于某一个来源, 所以照样能按来源替换 —— 判据是「有没有 enzyme_id 外键」,
不是「有没有 source_type」。

原实现在这里做了 **5 处无条件 `DELETE FROM <表>`(全表清空)**: 刷新 TrEMBL 会把
SwissProt 的子表行一并清掉, 来源隔离直接失效。按来源的删除已集中到 etl_run.purge_source(),
本模块只负责写入。

## 建表语句的唯一出处 = sql/schema.sql

原实现在本文件内嵌了 3 张表的 DDL, 与 `sql/schema.sql` 重复定义 ——
改 schema 时漏改一处, 本地建表语句就和 schema.sql 漂移。改成从 schema.sql 里取。
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL  # noqa: E402
from db_utils import schema_ddl  # noqa: E402
from sources import (columns_of, indexed_groups, indexed_width, read_segmented,  # noqa: E402
                     safe_usecols)

engine = create_engine(DB_URL)

MASTER_FILE = 'for_enzyme_detail/uniprotkb_master.tsv'
REFERENCES_FILE = 'for_enzyme_detail/child_tables/uniprotkb_references.tsv'
SEQ_LINKS_FILE = 'for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv'
GO_FILE = 'for_enzyme_detail/child_tables/uniprotkb_go.tsv'
ISOFORM_FILE = 'for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv'

# evidence 表没有 source_type, 但它的来源由 enzyme 决定 -> 审核状态跟着酶走。
SOURCE_TO_REVIEW = {'swiss_prot': 'official', 'trembl': 'pending'}

EVIDENCE_COLUMNS = {
    "title": "TEXT",
    "authors": "TEXT",
    "journal": "VARCHAR(300)",
    "volume": "VARCHAR(80)",
    "pages": "VARCHAR(80)",
    "publication_year": "INT",
    "reference_type": "VARCHAR(120)",
    "positions": "TEXT",
    "url": "VARCHAR(500)",
}


def _enzyme_source_map():
    """enzyme_id -> source_type。子表地址靠它判来源; 审核状态也跟着来源走。"""
    df = pd.read_sql("SELECT enzyme_id, source_type FROM enzyme", engine)
    return {str(r.enzyme_id): str(r.source_type) for r in df.itertuples()}


def _clean_value(value):
    if pd.isna(value):
        return None
    text_value = str(value).strip()
    if not text_value or text_value == "-":
        return None
    return text_value


def _clean_int(value):
    value = _clean_value(value)
    if not value:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_clean(row, columns):
    for column in columns:
        value = _clean_value(row.get(column))
        if value:
            return value
    return None


def _collect_indexed_links(row, id_prefix, link_prefix, max_index, category):
    links = []
    for i in range(1, max_index + 1):
        accession = _clean_value(row.get(f"{id_prefix}_{i}"))
        if not accession:
            continue
        links.append({
            "link_category": category,
            "accession": accession,
            "url": _clean_value(row.get(f"{link_prefix}_{i}")),
            "related_accession": None,
            "related_url": None,
        })
    return links


def _ensure_sequence_link_table():
    with engine.connect() as conn:
        conn.execute(text(schema_ddl('gene_sequence_link')))
        conn.execute(text("ALTER TABLE gene_sequence_link MODIFY COLUMN link_category VARCHAR(80) NOT NULL"))
        conn.commit()


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


def _ensure_table(ddl):
    with engine.connect() as conn:
        conn.execute(text(ddl))
        conn.commit()


def update_enzyme_from_master(only=None):
    """把 master 宽表里的序列 / 长度 / 质量回填到 enzyme 表。

    原实现逐行发一条 UPDATE, 语句还随行变化(不同行更新的列不同)—— 96k 行就是 96k 条语句。
    改成一条统一语句 + executemany, 并用 COALESCE 让「本次没有值」不覆盖已有值。
    """
    # 只读真正用得到的列: master 是**宽度动态**的宽表(实测 SP 141 列 / TrEMBL 473 列,
    # 全量 96k 行时列数还可能再涨), 而本步只用 Entry + 序列/长度/质量四列。
    # 全列解析 96k × 473 的峰值是 GB 级, 而这里要的只是 5 列。
    spec_cols = ["Entry", "Canonical Sequence", "Sequence Length", "Canonical Length", "Canonical Mass"]
    have = columns_of(MASTER_FILE, only=only)
    wanted = [c for c in spec_cols if all(c in names for names in have.values())]
    if "Entry" not in wanted or "Canonical Sequence" not in wanted:
        # 缺「预期的序列列名」时要走下面的列名猜测(扫全部列找序列样式的列), 那就必须读全列。
        wanted = None
    df = read_segmented(MASTER_FILE, only=only, dtype=str,
                        **({'usecols': wanted} if wanted else {}))

    # Use DB enzyme mapping
    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    seq_col = "Canonical Sequence" if "Canonical Sequence" in df.columns else None
    len_col = "Sequence Length" if "Sequence Length" in df.columns else None
    if not len_col and "Canonical Length" in df.columns:
        len_col = "Canonical Length"
    mass_col = "Canonical Mass" if "Canonical Mass" in df.columns else None

    if seq_col is None:
        for col in df.columns:
            sample = df[col].dropna().head(5)
            if len(sample) > 0:
                vals = sample.astype(str)
                if vals.str.len().mean() > 50 and vals.str.match(r'^[A-Z*]+$').all():
                    seq_col = col
                    break

    if seq_col is None:
        print("  enzyme update: sequence column not found, skipping")
        return

    params = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        seq = str(row[seq_col]) if pd.notna(row.get(seq_col)) else None
        length = _clean_int(row.get(len_col)) if len_col else None
        mass = None
        if mass_col and pd.notna(row.get(mass_col)):
            try:
                mass = float(str(row[mass_col]).replace(",", ""))
            except (ValueError, TypeError):
                mass = None

        if seq or length is not None or mass is not None:
            params.append({"enzyme_id": enzyme_id, "sequence": seq,
                           "length": length, "mass": mass})

    if not params:
        print("  enzyme (sequence update): 没有可回填的行")
        return

    # COALESCE: 本次没值(NULL)就不动原值 —— 用 SET sequence = :sequence 会把缺列的行写空。
    sql = text(
        "UPDATE enzyme SET "
        "sequence = COALESCE(:sequence, sequence), "
        "length   = COALESCE(:length, length), "
        "mass     = COALESCE(:mass, mass) "
        "WHERE enzyme_id = :enzyme_id"
    )
    with engine.begin() as conn:
        conn.execute(sql, params)

    print(f"  enzyme (sequence update): {len(params)} 行 (批量, 缺值不覆盖)")


def load_gene_info(only=None):
    """Load compact gene accession summary from sequence_links.tsv."""
    df = read_segmented(SEQ_LINKS_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    # The accession file has no gene symbol, but the master file does — and it is
    # keyed by the same UniProt entry, so the name is free here.
    cols = safe_usecols(MASTER_FILE, ["Entry", "Gene Names"], only=only)
    names_df = read_segmented(MASTER_FILE, only=only, dtype=str,
                              **({'usecols': cols} if cols else {}))
    gene_names = {
        entry: _clean_value(value)
        for entry, value in zip(names_df["Entry"], names_df["Gene Names"])
    }

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # 列宽按列头推导, 不写死 —— 原来这里是 range(1, 25) / range(1, 12),
        # 而现表最后一列恰好各有 1 条真实数据(零余量), 更宽的条目会取不到 accession。
        insdc_nuc_cols = [f"INSDC_Nuc_ID_{i}" for i in indexed_groups(df.columns, "INSDC_Nuc_ID")]
        insdc_genbank_cols = [f"INSDC_Nuc_GenBank_Link_{i}"
                              for i in indexed_groups(df.columns, "INSDC_Nuc_GenBank_Link")]
        insdc_prot_cols = [f"INSDC_Prot_ID_{i}" for i in indexed_groups(df.columns, "INSDC_Prot_ID")]
        refseq_nuc_cols = [f"RefSeq_Nuc_ID_{i}" for i in indexed_groups(df.columns, "RefSeq_Nuc_ID")]
        refseq_nuc_link_cols = [f"RefSeq_Nuc_Link_{i}"
                                for i in indexed_groups(df.columns, "RefSeq_Nuc_Link")]
        refseq_prot_cols = [f"RefSeq_Prot_ID_{i}" for i in indexed_groups(df.columns, "RefSeq_Prot_ID")]

        ena_accession = _first_clean(row, insdc_nuc_cols)
        genbank_id = ena_accession or _first_clean(row, refseq_nuc_cols)
        ncbi_url = _first_clean(row, insdc_genbank_cols) or _first_clean(row, refseq_nuc_link_cols)
        protein_accession = _first_clean(row, insdc_prot_cols) or _first_clean(row, refseq_prot_cols)

        if any([genbank_id, ena_accession, protein_accession]):
            rows.append({
                "enzyme_id": enzyme_id,
                "gene_name": gene_names.get(entry),
                "genbank_id": genbank_id,
                "ncbi_url": ncbi_url,
                "ena_accession": ena_accession,
                "protein_accession": protein_accession,
            })

    # 不再 DELETE FROM gene —— 那是全表清空, 会连另一个来源的行一起清掉。
    # 按来源的删除已在 etl_run.purge_source() 做完, 这里只写。
    if not rows:
        print("  gene: no rows to insert")
        return

    gene_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "gene_name", "genbank_id", "ncbi_url", "ena_accession", "protein_accession"]
    with engine.begin() as conn:
        gene_df[cols].to_sql("gene", conn, if_exists="append", index=False)
    print(f"  gene: {len(gene_df)} rows inserted")


def _append_sequence_link(rows, enzyme_id, category, accession, url=None, related_accession=None, related_url=None):
    accession = _clean_value(accession)
    if not accession:
        return
    rows.append({
        "enzyme_id": enzyme_id,
        "link_category": category,
        "accession": accession,
        "url": _clean_value(url),
        "related_accession": _clean_value(related_accession),
        "related_url": _clean_value(related_url),
    })


def _append_insdc_links(rows, enzyme_id, row, index):
    nuc_id = _clean_value(row.get(f"INSDC_Nuc_ID_{index}"))
    prot_id = _clean_value(row.get(f"INSDC_Prot_ID_{index}"))
    molecule = _clean_value(row.get(f"INSDC_Molecule_{index}"))
    suffix = f" ({molecule})" if molecule else ""

    for source in ("EMBL", "GenBank", "DDBJ"):
        _append_sequence_link(
            rows,
            enzyme_id,
            f"INSDC nucleotide {source}{suffix}",
            nuc_id,
            row.get(f"INSDC_Nuc_{source}_Link_{index}"),
            prot_id,
        )
        _append_sequence_link(
            rows,
            enzyme_id,
            f"INSDC protein {source}{suffix}",
            prot_id,
            row.get(f"INSDC_Prot_{source}_Link_{index}"),
            nuc_id,
        )

    if nuc_id and not any(_clean_value(row.get(f"INSDC_Nuc_{source}_Link_{index}")) for source in ("EMBL", "GenBank", "DDBJ")):
        _append_sequence_link(rows, enzyme_id, f"INSDC nucleotide{suffix}", nuc_id, related_accession=prot_id)
    if prot_id and not any(_clean_value(row.get(f"INSDC_Prot_{source}_Link_{index}")) for source in ("EMBL", "GenBank", "DDBJ")):
        _append_sequence_link(rows, enzyme_id, f"INSDC protein{suffix}", prot_id, related_accession=nuc_id)


def load_sequence_links(only=None):
    """Load all external sequence accessions from sequence_links.tsv."""
    _ensure_sequence_link_table()
    df = read_segmented(SEQ_LINKS_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # 宽度按列头推导, 不写死。这里是**会产生行**的一处 —— 写死 24/11 时,
        # 第 25 段 INSDC 链接会被静默丢掉(现表第 24 列恰好有 1 条真实数据, 零余量)。
        for i in indexed_groups(df.columns, "INSDC_Nuc_ID"):
            _append_insdc_links(rows, enzyme_id, row, i)

        for i in indexed_groups(df.columns, "RefSeq_Prot_ID"):
            protein_accession = _clean_value(row.get(f"RefSeq_Prot_ID_{i}"))
            nucleotide_accession = _clean_value(row.get(f"RefSeq_Nuc_ID_{i}"))
            protein_url = _clean_value(row.get(f"RefSeq_Prot_Link_{i}"))
            nucleotide_url = _clean_value(row.get(f"RefSeq_Nuc_Link_{i}"))
            molecule = _clean_value(row.get(f"RefSeq_Molecule_{i}"))
            suffix = f" ({molecule})" if molecule else ""
            _append_sequence_link(rows, enzyme_id, f"RefSeq protein{suffix}", protein_accession, protein_url, nucleotide_accession, nucleotide_url)
            _append_sequence_link(rows, enzyme_id, f"RefSeq nucleotide{suffix}", nucleotide_accession, nucleotide_url, protein_accession, protein_url)

    if not rows:
        print("  sequence links: no rows to insert")
        return

    link_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "link_category", "accession", "url", "related_accession", "related_url"]
    with engine.begin() as conn:
        link_df[cols].to_sql("gene_sequence_link", conn, if_exists="append", index=False)
    print(f"  sequence links: {len(link_df)} rows inserted")


def load_evidence(only=None):
    """Load evidence from references.tsv."""
    _ensure_columns("evidence", EVIDENCE_COLUMNS)
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE evidence MODIFY COLUMN positions TEXT"))
        conn.commit()
    df = read_segmented(REFERENCES_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))
    source_by_enzyme = _enzyme_source_map()

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # evidence 表没有 source_type, 审核状态跟着它那个酶的来源走 ——
        # 原来无条件写 "official", TrEMBL 的证据也会被标成官方审核。
        review = SOURCE_TO_REVIEW.get(source_by_enzyme.get(enzyme_id, ''))
        if review is None:
            raise ValueError(f'{enzyme_id} 的来源 {source_by_enzyme.get(enzyme_id)!r} 未知, '
                             f'无法确定 evidence.review_status; 已登记 {SOURCE_TO_REVIEW}')

        for i in indexed_groups(df.columns, "PMID"):
            evidence_row = {
                "enzyme_id": enzyme_id,
                "pubmed_id": _clean_value(row.get(f"PMID_{i}")),
                "doi": _clean_value(row.get(f"DOI_{i}")),
                "title": _clean_value(row.get(f"Title_{i}")),
                "authors": _clean_value(row.get(f"Authors_{i}")),
                "journal": _clean_value(row.get(f"Journal_{i}")),
                "volume": _clean_value(row.get(f"Volume_{i}")),
                "pages": _clean_value(row.get(f"Pages_{i}")),
                "publication_year": _clean_int(row.get(f"Year_{i}")),
                "reference_type": _clean_value(row.get(f"Type_{i}")),
                "positions": _clean_value(row.get(f"Positions_{i}")),
                "url": _clean_value(row.get(f"URL_{i}")),
                "source_description": "UniProt reference",
                "review_status": review,
            }
            if not any(v for k, v in evidence_row.items() if k not in {"enzyme_id", "source_description", "review_status"}):
                continue
            rows.append(evidence_row)

    if not rows:
        print("  evidence: no rows to insert")
        return

    ev_df = pd.DataFrame(rows).drop_duplicates()
    cols = [
        "enzyme_id", "pubmed_id", "doi", "title", "authors", "journal", "volume",
        "pages", "publication_year", "reference_type", "positions", "url",
        "source_description", "review_status",
    ]
    with engine.begin() as conn:
        ev_df[cols].to_sql("evidence", conn, if_exists="append", index=False)
    print(f"  evidence: {len(ev_df)} rows inserted")


def load_go_terms(only=None):
    """Load Gene Ontology annotations from uniprotkb_go.tsv."""
    _ensure_table(schema_ddl('enzyme_go'))
    df = read_segmented(GO_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        enzyme_id = entry_to_id.get(_clean_value(row.get("Entry")))
        if not enzyme_id:
            continue
        go_id = _clean_value(row.get("GO ID"))
        go_term = _clean_value(row.get("GO Term"))
        go_url = _clean_value(row.get("GO Link"))
        if go_id or go_term or go_url:
            rows.append({
                "enzyme_id": enzyme_id,
                "go_id": go_id,
                "go_term": go_term,
                "go_url": go_url,
            })

    if not rows:
        print("  GO terms: no rows to insert")
        return

    go_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "go_id", "go_term", "go_url"]
    with engine.begin() as conn:
        go_df[cols].to_sql("enzyme_go", conn, if_exists="append", index=False)
    print(f"  GO terms: {len(go_df)} rows inserted")


def load_isoforms(only=None):
    """Load isoform sequences from uniprotkb_isoform_sequences.tsv."""
    _ensure_table(schema_ddl('enzyme_isoform'))
    df = read_segmented(ISOFORM_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        enzyme_id = entry_to_id.get(_clean_value(row.get("Entry")))
        if not enzyme_id:
            continue
        isoform_id = _clean_value(row.get("Isoform_ID"))
        if not isoform_id:
            continue
        rows.append({
            "enzyme_id": enzyme_id,
            "isoform_id": isoform_id,
            "isoform_length": _clean_int(row.get("Isoform Length")),
            "isoform_mass": _clean_value(row.get("Isoform Mass")),
            "canonical_sequence": _clean_value(row.get("Canonical Sequence")),
            "canonical_length": _clean_int(row.get("Canonical Length")),
            "canonical_mass": _clean_value(row.get("Canonical Mass")),
            "sequence": _clean_value(row.get("Sequence")),
        })

    if not rows:
        print("  isoforms: no rows to insert")
        return

    isoform_df = pd.DataFrame(rows).drop_duplicates()
    cols = [
        "enzyme_id", "isoform_id", "isoform_length", "isoform_mass",
        "canonical_sequence", "canonical_length", "canonical_mass", "sequence",
    ]
    with engine.begin() as conn:
        isoform_df[cols].to_sql("enzyme_isoform", conn, if_exists="append", index=False)
    print(f"  isoforms: {len(isoform_df)} rows inserted")


def run(only=None):
    update_enzyme_from_master(only=only)
    load_gene_info(only=only)
    load_sequence_links(only=only)
    load_evidence(only=only)
    load_go_terms(only=only)
    load_isoforms(only=only)


if __name__ == "__main__":
    run()
