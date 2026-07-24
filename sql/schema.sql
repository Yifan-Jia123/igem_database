-- ============================================================
-- IGEM Metabolic Pathway Database — Schema DDL
-- Engine: MySQL 8.0+ / InnoDB / utf8mb4
-- ============================================================

CREATE DATABASE IF NOT EXISTS igem_terpene
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE igem_terpene;

-- ============================================================
-- 1. compound — 化合物节点
-- ============================================================
CREATE TABLE compound (
    compound_id         VARCHAR(30)     NOT NULL PRIMARY KEY
        COMMENT '库内化合物ID；有ChEBI时直接用CHEBI:xxx，否则内部编号',
    name                VARCHAR(500)    NOT NULL
        COMMENT '展示名称，优先常用名',
    chebi_id            VARCHAR(20)     NULL
        COMMENT 'ChEBI编号，如CHEBI:15377',
    formula             VARCHAR(200)    NULL
        COMMENT '化学式',
    charge              DECIMAL(6,2)    NULL
        COMMENT '净电荷',
    average_mass        DECIMAL(12,4)   NULL
        COMMENT '平均分子量',
    smiles              TEXT            NULL
        COMMENT 'SMILES表达式',
    inchi               TEXT            NULL
        COMMENT 'InChI表达式',
    structure_image_url VARCHAR(500)    NULL
        COMMENT '化合物结构图URL/SVG',
    chebi_url           VARCHAR(500)    NULL
        COMMENT 'ChEBI外部链接',
    description         VARCHAR(1000)   NULL
        COMMENT '简短描述',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_compound_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 2. enzyme — 酶静态属性
-- ============================================================
CREATE TABLE enzyme (
    enzyme_id           VARCHAR(20)     NOT NULL PRIMARY KEY
        COMMENT '本库酶编号，格式ENZ000001',
    uniprot_id          VARCHAR(20)     NULL
        COMMENT 'UniProt Entry编号',
    primary_name        VARCHAR(500)    NOT NULL
        COMMENT '酶主要名称',
    secondary_names     JSON            NULL
        COMMENT '别名字符串数组，如["name1","name2"]',
    organism_name       VARCHAR(300)    NULL
        COMMENT '物种来源',
    sequence            TEXT            NULL
        COMMENT '氨基酸序列',
    length              INT             NULL
        COMMENT '序列长度',
    mass                DECIMAL(12,2)   NULL
        COMMENT '分子量(Da)',
    source_type         ENUM(
                            'swiss_prot',
                            'trembl',
                            'ai_literature',
                            'manual_literature'
                        )               NOT NULL DEFAULT 'swiss_prot'
        COMMENT '数据来源',
    review_status       ENUM(
                            'pending',
                            'reviewed',
                            'official',
                            'deprecated'
                        )               NOT NULL DEFAULT 'official'
        COMMENT '审核状态',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_uniprot_id (uniprot_id),
    INDEX idx_enzyme_name (primary_name),
    INDEX idx_organism (organism_name),
    INDEX idx_source_type (source_type),
    INDEX idx_review_status (review_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 3. gene — 基因信息（酶的可选补充）
-- ============================================================
CREATE TABLE gene (
    gene_id             INT             NOT NULL AUTO_INCREMENT PRIMARY KEY,
    enzyme_id           VARCHAR(20)     NOT NULL
        COMMENT '关联的酶ID',
    gene_name           VARCHAR(200)    NULL
        COMMENT '基因名称',
    genbank_id          VARCHAR(50)     NULL
        COMMENT 'GenBank或RefSeq编号',
    ncbi_url            VARCHAR(500)    NULL
        COMMENT 'NCBI外部链接',
    ena_accession       VARCHAR(50)     NULL
        COMMENT 'ENA/EMBL编号',
    protein_accession   VARCHAR(50)     NULL
        COMMENT '蛋白编号(NCBI)',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_gene_enzyme (enzyme_id),
    INDEX idx_gene_name (gene_name),
    INDEX idx_genbank_id (genbank_id),
    CONSTRAINT fk_gene_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 4. reaction — 反应事实
-- ============================================================
CREATE TABLE reaction (
    reaction_id         VARCHAR(30)     NOT NULL PRIMARY KEY
        COMMENT '库内反应ID；有Rhea时直接用RHEA:xxx，否则内部编号',
    rhea_id             VARCHAR(20)     NULL
        COMMENT 'Rhea反应编号',
    equation            TEXT            NOT NULL
        COMMENT '反应方程式',
    direction           ENUM(
                            'forward',
                            'reverse',
                            'reversible',
                            'unknown'
                        )               NOT NULL DEFAULT 'unknown'
        COMMENT '反应方向',
    ec_number           VARCHAR(50)     NULL
        COMMENT 'EC编号',
    smiles              TEXT            NULL
        COMMENT '反应SMILES',
    rhea_url            VARCHAR(500)    NULL
        COMMENT 'Rhea外部链接',
    atom_map_image_url  VARCHAR(500)    NULL
        COMMENT '由Rhea ID生成的atom map SVG URL',
    source_type         ENUM(
                            'swiss_prot',
                            'trembl',
                            'ai_literature',
                            'manual_literature'
                        )               NOT NULL DEFAULT 'swiss_prot',
    review_status       ENUM(
                            'pending',
                            'reviewed',
                            'official',
                            'deprecated'
                        )               NOT NULL DEFAULT 'official',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_rhea_id (rhea_id),
    INDEX idx_ec_number (ec_number),
    INDEX idx_reaction_source (source_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 5. reaction_compound — 反应↔化合物 底物/产物关系
-- ============================================================
CREATE TABLE reaction_compound (
    id                  INT             NOT NULL AUTO_INCREMENT PRIMARY KEY,
    reaction_id         VARCHAR(30)     NOT NULL,
    compound_id         VARCHAR(30)     NOT NULL,
    role                ENUM(
                            'substrate',
                            'product'
                        )               NOT NULL
        COMMENT '底物或产物',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_rc_unique (reaction_id, compound_id, role),
    INDEX idx_rc_reaction (reaction_id),
    INDEX idx_rc_compound (compound_id),
    CONSTRAINT fk_rc_reaction
        FOREIGN KEY (reaction_id) REFERENCES reaction(reaction_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_rc_compound
        FOREIGN KEY (compound_id) REFERENCES compound(compound_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 6. enzyme_reaction_edge — 图中的边
--     一条边 = 一个酶催化一个具体反应
--     一个酶可催化多反应，一个反应可由多酶催化
-- ============================================================
CREATE TABLE enzyme_reaction_edge (
    edge_id             VARCHAR(20)     NOT NULL PRIMARY KEY
        COMMENT '边ID，格式EDGE000001',
    enzyme_id           VARCHAR(20)     NOT NULL,
    reaction_id         VARCHAR(30)     NOT NULL,
    source_type         ENUM(
                            'swiss_prot',
                            'trembl',
                            'ai_literature',
                            'manual_literature'
                        )               NOT NULL DEFAULT 'swiss_prot'
        COMMENT '边来源标签（边和酶可不同来源）',
    review_status       ENUM(
                            'pending',
                            'reviewed',
                            'official',
                            'deprecated'
                        )               NOT NULL DEFAULT 'official',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_edge_unique (enzyme_id, reaction_id),
    INDEX idx_edge_enzyme (enzyme_id),
    INDEX idx_edge_reaction (reaction_id),
    INDEX idx_edge_source (source_type),
    CONSTRAINT fk_edge_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_edge_reaction
        FOREIGN KEY (reaction_id) REFERENCES reaction(reaction_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 7. evidence — 文献证据
--     支撑AI/人工补充数据的可信度
-- ============================================================
CREATE TABLE evidence (
    evidence_id         INT             NOT NULL AUTO_INCREMENT PRIMARY KEY,
    enzyme_id           VARCHAR(20)     NOT NULL
        COMMENT '关联的酶ID',
    doi                 VARCHAR(200)    NULL
        COMMENT 'DOI编号',
    pubmed_id           VARCHAR(20)     NULL
        COMMENT 'PubMed ID',
    source_description  VARCHAR(500)    NULL
        COMMENT '抽取方式/来源说明',
    review_status       ENUM(
                            'pending',
                            'reviewed',
                            'official',
                            'deprecated'
                        )               NOT NULL DEFAULT 'pending',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_evidence_enzyme (enzyme_id),
    INDEX idx_evidence_doi (doi),
    INDEX idx_evidence_pubmed (pubmed_id),
    CONSTRAINT fk_evidence_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 8. pathway_cache — 热门通路结果缓存（可选）
--     通路结果由应用层实时计算，此表仅用于缓存高频查询
-- ============================================================
CREATE TABLE pathway_cache (
    cache_id            INT             NOT NULL AUTO_INCREMENT PRIMARY KEY,
    start_compound_id   VARCHAR(30)     NOT NULL,
    end_compound_id     VARCHAR(30)     NOT NULL,
    via_compound_ids    JSON            NULL
        COMMENT '中间化合物ID数组',
    max_steps           INT             NOT NULL DEFAULT 6,
    pathway_json        JSON            NOT NULL
        COMMENT '通路结果JSON',
    hit_count           INT             NOT NULL DEFAULT 1
        COMMENT '命中次数，用于缓存淘汰',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_cache_query (start_compound_id, end_compound_id),
    INDEX idx_cache_hit (hit_count DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
