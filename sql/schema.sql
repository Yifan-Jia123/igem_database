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
    -- Second InChI Key (RDKit computes it from smiles on the fly). Used only as a
    -- match fallback for /ketcher/search, never appears in any API response.
    -- inchi_key is the authoritative ChEBI value.
    -- Existing databases must add this column and index by hand
    -- (etl_compounds._ensure_columns adds columns only, it does not build indexes):
    --   ALTER TABLE compound
    --     ADD COLUMN inchi_key_derived VARCHAR(100) NULL AFTER inchi_key,
    --     ADD INDEX idx_compound_inchi_key_derived (inchi_key_derived);
    inchi_key_derived VARCHAR(100),
    structure_image_url VARCHAR(500),
    chebi_url VARCHAR(500),
    description VARCHAR(1000),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_compound_chebi_id (chebi_id),
    INDEX idx_compound_inchi_key (inchi_key),
    INDEX idx_compound_inchi_key_derived (inchi_key_derived)
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

-- Per-enzyme model reference score + membrane annotation. Granularity: rows with
-- isoform_id IS NULL are the canonical sequence's score, rows with a non-null
-- isoform_id are that variant's own score.
--
-- Why a separate table: sequences are shared (no true variant differs only in its
-- score), so putting these columns on `enzyme` would rewrite the hot table every
-- time the model is re-run.
--
-- evidence column width 1024 is measured: Membrane Evidence maxes out at 530 chars
-- (18,616 non-null rows), so copying enzyme_go.go_term's 500 would truncate.
-- deep_solnet_score DECIMAL(7,6): longest measured value is 8 chars with 6 decimals,
-- just enough to lose no precision.
CREATE TABLE IF NOT EXISTS enzyme_solubility_score (
    solubility_record_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    isoform_id VARCHAR(80),
    deep_solnet_score DECIMAL(7,6),
    membrane VARCHAR(20),
    membrane_evidence VARCHAR(1024),
    sequence_length INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_solubility_score_enzyme (enzyme_id),
    INDEX idx_enzyme_solubility_score_value (deep_solnet_score),
    CONSTRAINT fk_enzyme_solubility_score_enzyme
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
    -- The only usable index for prefix-segment queries (field_value LIKE 'abc%'). Without
    -- it that segment is a full scan -- measured on the full database at 6.6-7.1s to
    -- return 2-192 rows; with it, 0.013-0.098s, row-for-row identical results.
    -- Prefix length 64 chars: the index is 49 B/row (~177MB / 3.76M rows), far cheaper
    -- than a B-tree over the full field_value, while locating a LIKE 'x%' needs only the
    -- first 64 chars.
    -- Note '%x%' (contains segment) cannot use any B-tree and is still a full scan --
    -- that is a separate matter, see search_service.
    INDEX idx_search_index_value_prefix (field_value(64)),
    CONSTRAINT fk_search_index_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- ID persistence. Its lifecycle is the opposite of every table above: this table is
-- append-only, never part of any DELETE / TRUNCATE -- it is the only basis for
-- "an existing enzyme's ID never changes".
--
-- Deliberately no FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id):
-- when an entry disappears from a new UniProt release, its enzyme row is deleted by
-- source replacement, but the mapping row must survive, or that ID would be
-- reassigned to a different entry.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enzyme_id_map (
    uniprot_id VARCHAR(20) PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL UNIQUE,
    first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at DATETIME NULL,          -- marked when the entry vanishes from the data; the ID is not recycled
    INDEX idx_enzyme_id_map_enzyme (enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- When UniProt entries merge, the old accession is demoted to secondary. This table
-- carries the ID across, so the same biological entity is not treated as a new enzyme
-- and issued a new ID just because its accession changed.
CREATE TABLE IF NOT EXISTS enzyme_alias_map (
    secondary_accession VARCHAR(20) PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_alias_enzyme (enzyme_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
