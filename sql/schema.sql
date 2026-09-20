CREATE DATABASE IF NOT EXISTS igem_terpene
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE igem_terpene;

CREATE TABLE IF NOT EXISTS compound (
    compound_id VARCHAR(30) PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    chebi_id VARCHAR(20),
    formula VARCHAR(200),
    charge DECIMAL(6, 2),
    average_mass DECIMAL(12, 4),
    smiles TEXT,
    inchi TEXT,
    inchi_key VARCHAR(100),
    structure_image_url VARCHAR(500),
    chebi_url VARCHAR(500),
    description VARCHAR(1000),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_compound_chebi_id (chebi_id),
    INDEX idx_compound_inchi_key (inchi_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enzyme (
    enzyme_id VARCHAR(20) PRIMARY KEY,
    uniprot_id VARCHAR(20) UNIQUE,
    primary_name VARCHAR(500) NOT NULL,
    secondary_names JSON,
    organism_name VARCHAR(300),
    sequence TEXT,
    length INT,
    mass DECIMAL(12, 2),
    source_type ENUM('swiss_prot', 'trembl', 'ai_literature', 'manual_literature') DEFAULT 'swiss_prot',
    review_status ENUM('pending', 'reviewed', 'official', 'deprecated') DEFAULT 'official',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_enzyme_uniprot_id (uniprot_id),
    INDEX idx_enzyme_organism (organism_name),
    INDEX idx_enzyme_source_review (source_type, review_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS gene (
    gene_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    gene_name VARCHAR(200),
    genbank_id VARCHAR(50),
    ncbi_url VARCHAR(500),
    ena_accession VARCHAR(50),
    protein_accession VARCHAR(50),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_gene_enzyme (enzyme_id),
    INDEX idx_gene_accessions (genbank_id, ena_accession, protein_accession),
    CONSTRAINT fk_gene_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS gene_sequence_link (
    sequence_link_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    link_category VARCHAR(80) NOT NULL,
    accession VARCHAR(80) NOT NULL,
    url VARCHAR(500),
    related_accession VARCHAR(80),
    related_url VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_gene_sequence_link_enzyme (enzyme_id),
    INDEX idx_gene_sequence_link_accession (accession),
    CONSTRAINT fk_gene_sequence_link_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS reaction (
    reaction_id VARCHAR(30) PRIMARY KEY,
    rhea_id VARCHAR(20) UNIQUE,
    equation TEXT NOT NULL,
    direction ENUM('forward', 'reverse', 'reversible', 'unknown') DEFAULT 'unknown',
    ec_number VARCHAR(50),
    smiles TEXT,
    rhea_url VARCHAR(500),
    atom_map_image_url VARCHAR(500),
    source_type ENUM('swiss_prot', 'trembl', 'ai_literature', 'manual_literature') DEFAULT 'swiss_prot',
    review_status ENUM('pending', 'reviewed', 'official', 'deprecated') DEFAULT 'official',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_reaction_rhea_id (rhea_id),
    INDEX idx_reaction_ec_number (ec_number),
    INDEX idx_reaction_source_review (source_type, review_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS reaction_compound (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reaction_id VARCHAR(30) NOT NULL,
    compound_id VARCHAR(30) NOT NULL,
    role ENUM('substrate', 'product') NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_reaction_compound_reaction (reaction_id),
    INDEX idx_reaction_compound_compound (compound_id),
    UNIQUE KEY uq_reaction_compound_role (reaction_id, compound_id, role),
    CONSTRAINT fk_reaction_compound_reaction
        FOREIGN KEY (reaction_id) REFERENCES reaction(reaction_id),
    CONSTRAINT fk_reaction_compound_compound
        FOREIGN KEY (compound_id) REFERENCES compound(compound_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enzyme_reaction_edge (
    edge_id VARCHAR(20) PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    reaction_id VARCHAR(30) NOT NULL,
    source_type ENUM('swiss_prot', 'trembl', 'ai_literature', 'manual_literature') DEFAULT 'swiss_prot',
    review_status ENUM('pending', 'reviewed', 'official', 'deprecated') DEFAULT 'official',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_edge_enzyme (enzyme_id),
    INDEX idx_edge_reaction (reaction_id),
    INDEX idx_edge_source_review (source_type, review_status),
    UNIQUE KEY uq_edge_enzyme_reaction (enzyme_id, reaction_id),
    CONSTRAINT fk_edge_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id),
    CONSTRAINT fk_edge_reaction
        FOREIGN KEY (reaction_id) REFERENCES reaction(reaction_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    doi VARCHAR(200),
    pubmed_id VARCHAR(20),
    title TEXT,
    authors TEXT,
    journal VARCHAR(300),
    volume VARCHAR(80),
    pages VARCHAR(80),
    publication_year INT,
    reference_type VARCHAR(120),
    positions TEXT,
    url VARCHAR(500),
    source_description VARCHAR(500),
    review_status ENUM('pending', 'reviewed', 'official', 'deprecated') DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_evidence_enzyme (enzyme_id),
    INDEX idx_evidence_pubmed (pubmed_id),
    INDEX idx_evidence_doi (doi),
    INDEX idx_evidence_year (publication_year),
    CONSTRAINT fk_evidence_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enzyme_go (
    go_record_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    go_id VARCHAR(30),
    go_term VARCHAR(500),
    go_url VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_go_enzyme (enzyme_id),
    INDEX idx_enzyme_go_id (go_id),
    CONSTRAINT fk_enzyme_go_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enzyme_isoform (
    isoform_record_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    isoform_id VARCHAR(80),
    isoform_length INT,
    isoform_mass VARCHAR(80),
    canonical_sequence TEXT,
    canonical_length INT,
    canonical_mass VARCHAR(80),
    sequence TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_isoform_enzyme (enzyme_id),
    INDEX idx_enzyme_isoform_id (isoform_id),
    CONSTRAINT fk_enzyme_isoform_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pathway_cache (
    cache_id INT AUTO_INCREMENT PRIMARY KEY,
    start_compound_id VARCHAR(30) NOT NULL,
    end_compound_id VARCHAR(30) NOT NULL,
    via_compound_ids JSON,
    max_steps INT DEFAULT 6,
    pathway_json JSON NOT NULL,
    hit_count INT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_pathway_cache_query (start_compound_id, end_compound_id, max_steps)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS search_index (
    search_index_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type VARCHAR(30) NOT NULL,
    entity_id VARCHAR(80) NOT NULL,
    enzyme_id VARCHAR(20),
    source_file VARCHAR(160) NOT NULL,
    field_name VARCHAR(80) NOT NULL,
    field_value TEXT NOT NULL,
    field_value_hash CHAR(40) NOT NULL,
    weight INT NOT NULL DEFAULT 10,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_search_index_entity (entity_type, entity_id),
    INDEX idx_search_index_enzyme (enzyme_id),
    INDEX idx_search_index_field (field_name),
    INDEX idx_search_index_hash (field_value_hash),
    -- 前缀段查询 (field_value LIKE 'abc%') 的唯一可用索引。没有它时该段是全表扫,
    -- 全量库实测 6.6-7.1s 只为返回 2-192 行; 加上后 0.013-0.098s, 结果逐行相同。
    -- 前缀长度 64 字符: 索引 49 B/行 (~177MB / 376 万行), 比 field_value 全长的
    -- B-tree 代价低得多, 而对 LIKE 'x%' 的定位只需前 64 字符。
    -- 注意 '%x%'(包含段) 无法用任何 B-tree, 仍是全表扫 —— 那是另一件事, 见 search_service。
    INDEX idx_search_index_value_prefix (field_value(64)),
    CONSTRAINT fk_search_index_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 编号持久化。生命周期与上面所有表相反: 本表只增不删,
-- 永不参与任何 DELETE / TRUNCATE —— 它是「已有酶的编号永远不变」的唯一依据。
--
-- 刻意不加 FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id):
-- 条目从新版本 UniProt 消失时, enzyme 行会被按来源替换删掉,
-- 但映射行必须活下来, 否则该编号会被重新分配给别的条目。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enzyme_id_map (
    uniprot_id VARCHAR(20) PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL UNIQUE,
    first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at DATETIME NULL,          -- 条目从数据中消失时打标, 编号不回收
    INDEX idx_enzyme_id_map_enzyme (enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- UniProt 条目合并时旧 accession 降为 secondary。靠这张表把编号接续过去,
-- 避免同一个生物学实体因为改号而被当成新酶发一个新号。
CREATE TABLE IF NOT EXISTS enzyme_alias_map (
    secondary_accession VARCHAR(20) PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_alias_enzyme (enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
