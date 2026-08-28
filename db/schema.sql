-- =====================================================================
-- Lost & Found Intelligent Matching Platform — PostgreSQL Schema V1.0
--
-- 五层：
--   1 Business      item_records / lost_reports / found_reports / return_records
--   2 Master Data   item_categories / brands / locations / attribute_definitions
--   3 AI Understand ai_analyses / item_attributes
--   4 AI Retrieval  embeddings / matching_runs
--   5 AI Decision   match_candidates / match_evidences / match_decisions
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------
-- 1) Master data
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_user_id    VARCHAR(200),
    name                VARCHAR(200),
    email               VARCHAR(300),
    phone               VARCHAR(50),
    role                VARCHAR(50)  NOT NULL DEFAULT 'USER',   -- USER / STAFF / ADMIN
    status              VARCHAR(30)  NOT NULL DEFAULT 'ACTIVE',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS item_categories (
    id                  BIGSERIAL PRIMARY KEY,
    parent_id           BIGINT REFERENCES item_categories(id),
    code                VARCHAR(100) UNIQUE NOT NULL,
    name                VARCHAR(200) NOT NULL,
    level               INTEGER      NOT NULL DEFAULT 1,
    -- 该类别的匹配调参：地点半径(米) / 时间常数(小时)
    location_tau_m      NUMERIC(10,2),
    time_tau_hours      NUMERIC(10,2),
    active              BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS brands (
    id                  BIGSERIAL PRIMARY KEY,
    name                VARCHAR(200) NOT NULL,
    normalized_name     VARCHAR(200) NOT NULL,
    aliases             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    category_id         BIGINT REFERENCES item_categories(id)
);
CREATE INDEX IF NOT EXISTS idx_brands_normalized ON brands(normalized_name);

-- 地点树：日本 -> 东京 -> 新宿区 -> 新宿站 -> JR -> 南口
CREATE TABLE IF NOT EXISTS locations (
    id                  BIGSERIAL PRIMARY KEY,
    name                VARCHAR(300) NOT NULL,
    normalized_name     VARCHAR(300),
    aliases             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    location_type       VARCHAR(50),      -- COUNTRY/CITY/WARD/STATION/FACILITY/EXIT/...
    parent_id           BIGINT REFERENCES locations(id),
    latitude            NUMERIC(10,7),
    longitude           NUMERIC(10,7),
    external_id         VARCHAR(200),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_id);

-- 系统支持哪些属性（category-aware，带匹配权重）
CREATE TABLE IF NOT EXISTS attribute_definitions (
    id                  BIGSERIAL PRIMARY KEY,
    category_id         BIGINT NOT NULL REFERENCES item_categories(id),
    attribute_code      VARCHAR(100) NOT NULL,
    attribute_name      VARCHAR(200) NOT NULL,
    data_type           VARCHAR(30)  NOT NULL DEFAULT 'TEXT',  -- TEXT/NUMBER/BOOLEAN/JSON
    searchable          BOOLEAN      NOT NULL DEFAULT TRUE,
    matchable           BOOLEAN      NOT NULL DEFAULT TRUE,
    -- 属性级重要性权重（手机 IMEI=10, color=2 ...）
    importance_weight   NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    -- 冲突等级：CRITICAL / MAJOR / MINOR / NONE
    conflict_severity   VARCHAR(20)  NOT NULL DEFAULT 'MINOR',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(category_id, attribute_code)
);

-- ---------------------------------------------------------------------
-- 2) Business：统一物品表 + 丢失/拾获事件
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS item_records (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_type         VARCHAR(20)  NOT NULL,
    status              VARCHAR(30)  NOT NULL DEFAULT 'ACTIVE',
    category_id         BIGINT REFERENCES item_categories(id),
    brand               VARCHAR(100),
    model               VARCHAR(200),
    -- 原始描述：永远不被 AI 覆盖
    raw_description     TEXT         NOT NULL,
    normalized_text     TEXT,
    search_vector       tsvector,
    created_by          UUID REFERENCES users(id),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version             INTEGER      NOT NULL DEFAULT 1,
    CONSTRAINT chk_record_type CHECK (record_type IN ('LOST', 'FOUND')),
    CONSTRAINT chk_item_status CHECK (status IN
        ('ACTIVE','MATCHED','CLAIMED','RETURNED','ARCHIVED','DISPOSED'))
);
CREATE INDEX IF NOT EXISTS idx_item_records_type     ON item_records(record_type);
CREATE INDEX IF NOT EXISTS idx_item_records_status   ON item_records(status);
CREATE INDEX IF NOT EXISTS idx_item_records_category ON item_records(category_id);
CREATE INDEX IF NOT EXISTS idx_item_records_created  ON item_records(created_at);
CREATE INDEX IF NOT EXISTS idx_item_search           ON item_records USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_item_trgm             ON item_records USING GIN(normalized_text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS lost_reports (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id               UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    lost_at               TIMESTAMPTZ,
    -- 用户说的是「昨晚 7~9 点之间」，不是 19:32:15
    lost_at_start         TIMESTAMPTZ,
    lost_at_end           TIMESTAMPTZ,
    lost_location_id      BIGINT REFERENCES locations(id),
    last_seen_location_id BIGINT REFERENCES locations(id),
    circumstances         TEXT,
    reported_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reported_by           UUID REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_lost_reports_item     ON lost_reports(item_id);
CREATE INDEX IF NOT EXISTS idx_lost_reports_location ON lost_reports(lost_location_id);
CREATE INDEX IF NOT EXISTS idx_lost_reports_lost_at  ON lost_reports(lost_at);

CREATE TABLE IF NOT EXISTS found_reports (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id             UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    found_at            TIMESTAMPTZ,
    found_location_id   BIGINT REFERENCES locations(id),
    found_by            UUID REFERENCES users(id),
    storage_location    VARCHAR(500),
    custody_status      VARCHAR(30) NOT NULL DEFAULT 'IN_CUSTODY',
    reported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_found_reports_item     ON found_reports(item_id);
CREATE INDEX IF NOT EXISTS idx_found_reports_location ON found_reports(found_location_id);
CREATE INDEX IF NOT EXISTS idx_found_reports_found_at ON found_reports(found_at);

-- ---------------------------------------------------------------------
-- 3) AI Understanding
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS item_attributes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id             UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    attribute_id        BIGINT REFERENCES attribute_definitions(id),
    attribute_code      VARCHAR(100) NOT NULL,
    value_text          TEXT,
    value_number        NUMERIC,
    value_boolean       BOOLEAN,
    value_json          JSONB,
    original_value      TEXT,
    -- USER / AI / ADMIN / OCR / VISION / IMPORT / SYSTEM
    source              VARCHAR(30)  NOT NULL DEFAULT 'AI',
    -- EXPLICIT / INFERRED / UNCERTAIN
    source_type         VARCHAR(20)  NOT NULL DEFAULT 'EXPLICIT',
    confidence          NUMERIC(5,4),
    is_secret           BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_item_attributes_item ON item_attributes(item_id);
CREATE INDEX IF NOT EXISTS idx_item_attributes_code ON item_attributes(attribute_code);
CREATE INDEX IF NOT EXISTS idx_item_attributes_val  ON item_attributes(attribute_code, value_text);

CREATE TABLE IF NOT EXISTS item_images (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id             UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    storage_url         TEXT NOT NULL,          -- S3 URL / object key，不存二进制
    image_type          VARCHAR(30),
    ocr_text            TEXT,
    vision_description  TEXT,
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id);

CREATE TABLE IF NOT EXISTS ai_analyses (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id             UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    analysis_type       VARCHAR(50) NOT NULL,   -- ATTRIBUTE_EXTRACTION / QUERY_UNDERSTANDING / ...
    model_provider      VARCHAR(50),
    model_name          VARCHAR(100),
    prompt_version      VARCHAR(50),
    input_hash          VARCHAR(128),
    result_json         JSONB NOT NULL,
    confidence          NUMERIC(5,4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_item ON ai_analyses(item_id, analysis_type);

-- ---------------------------------------------------------------------
-- 4) AI Retrieval
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS embeddings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id             UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    embedding_type      VARCHAR(50)  NOT NULL,   -- TEXT / ATTRIBUTES / IMAGE
    model_provider      VARCHAR(50),
    model_name          VARCHAR(100) NOT NULL,
    model_version       VARCHAR(50)  NOT NULL DEFAULT 'v1',
    dimensions          INTEGER      NOT NULL,
    content_text        TEXT,
    content_hash        CHAR(64),
    embedding           VECTOR(1536) NOT NULL,
    -- ACTIVE / DEPRECATED / FAILED / PROCESSING：模型升级并存而非覆盖
    status              VARCHAR(30)  NOT NULL DEFAULT 'ACTIVE',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (item_id, embedding_type, model_name, model_version)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_item ON embeddings(item_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_lookup ON embeddings(embedding_type, status);
CREATE INDEX IF NOT EXISTS idx_embeddings_vector
    ON embeddings USING hnsw (embedding vector_cosine_ops);

-- 每次匹配跑批的可追溯记录：回答「为什么当时没匹配出来」
CREATE TABLE IF NOT EXISTS matching_runs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trigger_type        VARCHAR(50),            -- LOST_CREATED / FOUND_CREATED / MANUAL / BATCH
    source_item_id      UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    algorithm_version   VARCHAR(50),
    embedding_model     VARCHAR(100),
    retrieval_config    JSONB,
    ranking_config      JSONB,
    candidate_count     INTEGER,
    duration_ms         INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_matching_runs_source ON matching_runs(source_item_id);

-- ---------------------------------------------------------------------
-- 5) AI Decision
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS match_candidates (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lost_item_id        UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    found_item_id       UUID NOT NULL REFERENCES item_records(id) ON DELETE CASCADE,
    run_id              UUID REFERENCES matching_runs(id),
    -- 召回分数与最终匹配分数必须分开
    retrieval_score     NUMERIC(8,5),
    category_score      NUMERIC(8,5),
    attribute_score     NUMERIC(8,5),
    location_score      NUMERIC(8,5),
    time_score          NUMERIC(8,5),
    distinctive_score   NUMERIC(8,5),
    semantic_score      NUMERIC(8,5),
    keyword_score       NUMERIC(8,5),
    image_score         NUMERIC(8,5),
    conflict_penalty    NUMERIC(8,5) NOT NULL DEFAULT 0,
    evidence_bonus      NUMERIC(8,5) NOT NULL DEFAULT 0,
    final_score         NUMERIC(8,5),
    confidence          NUMERIC(5,4),
    match_level         VARCHAR(20),   -- VERY_HIGH/HIGH/MEDIUM/LOW/IGNORE/REJECT
    llm_decision        VARCHAR(30),   -- MATCH/LIKELY_MATCH/POSSIBLE_MATCH/UNLIKELY_MATCH/NOT_MATCH
    llm_confidence      NUMERIC(5,4),
    recommended_action  VARCHAR(30),   -- AUTO_RECOMMEND/HUMAN_REVIEW/DO_NOT_RECOMMEND
    status              VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    algorithm_version   VARCHAR(50),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (lost_item_id, found_item_id)
);
CREATE INDEX IF NOT EXISTS idx_match_candidates_lost  ON match_candidates(lost_item_id, final_score DESC);
CREATE INDEX IF NOT EXISTS idx_match_candidates_found ON match_candidates(found_item_id, final_score DESC);
CREATE INDEX IF NOT EXISTS idx_match_candidates_level ON match_candidates(match_level, status);

-- 「为什么匹配」：AI Explainability 的唯一真实来源
CREATE TABLE IF NOT EXISTS match_evidences (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id        UUID NOT NULL REFERENCES match_candidates(id) ON DELETE CASCADE,
    evidence_type       VARCHAR(50) NOT NULL,   -- ATTRIBUTE/LOCATION/TIME/SEMANTIC/KEYWORD/IMAGE/DISTINCTIVE/CATEGORY
    field_name          VARCHAR(100),
    lost_value          TEXT,
    found_value         TEXT,
    -- EXACT_MATCH/SEMANTIC_MATCH/PARTIAL_MATCH/UNKNOWN/MINOR_CONFLICT/MAJOR_CONFLICT/CRITICAL_CONFLICT
    relation            VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN',
    similarity_score    NUMERIC(8,5),
    weight              NUMERIC(8,5),
    reliability         NUMERIC(5,4),
    contribution        NUMERIC(8,5),
    is_conflict         BOOLEAN NOT NULL DEFAULT FALSE,
    severity            VARCHAR(20),
    explanation         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_match_evidences_candidate ON match_evidences(candidate_id);

-- 人最终怎么判断：同时是 AI Feedback Loop 的训练数据
CREATE TABLE IF NOT EXISTS match_decisions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id        UUID NOT NULL REFERENCES match_candidates(id) ON DELETE CASCADE,
    decision            VARCHAR(30) NOT NULL,   -- CONFIRMED / REJECTED / DEFERRED
    decided_by          UUID REFERENCES users(id),
    decided_by_role     VARCHAR(30),
    reason              TEXT,
    score_at_decision   NUMERIC(8,5),
    decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_match_decisions_candidate ON match_decisions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_match_decisions_decision  ON match_decisions(decision);

CREATE TABLE IF NOT EXISTS return_records (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id              UUID NOT NULL REFERENCES item_records(id),
    matched_candidate_id UUID REFERENCES match_candidates(id),
    returned_to          UUID REFERENCES users(id),
    returned_by          UUID REFERENCES users(id),
    -- ID_CARD / SECRET_ATTRIBUTE / SERIAL_NUMBER / USER_PROOF / STAFF_CONFIRMATION
    verification_method  VARCHAR(50),
    verification_result  BOOLEAN,
    returned_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_id            UUID,
    action              VARCHAR(100) NOT NULL,
    entity_type         VARCHAR(100),
    entity_id           UUID,
    before_data         JSONB,
    after_data          JSONB,
    ip_address          INET,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(entity_type, entity_id);

-- ---------------------------------------------------------------------
-- 全文检索向量自动维护
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION item_records_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        to_tsvector('simple',
            coalesce(NEW.normalized_text, '') || ' ' ||
            coalesce(NEW.raw_description, '') || ' ' ||
            coalesce(NEW.brand, '')           || ' ' ||
            coalesce(NEW.model, ''));
    NEW.updated_at := NOW();
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_item_records_tsv ON item_records;
CREATE TRIGGER trg_item_records_tsv
    BEFORE INSERT OR UPDATE ON item_records
    FOR EACH ROW EXECUTE FUNCTION item_records_tsv_trigger();
