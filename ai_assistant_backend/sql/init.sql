-- ============================================================================
--  智语 AI · 企业知识库对话助手 —— 数据库初始化脚本
-- ============================================================================
--  目标环境：PostgreSQL 17 + pgvector 0.8
--
--  用法：
--      createdb zhiyu_ai
--      psql -d zhiyu_ai -f sql/init.sql
--
--  本脚本建出项目运行所需的全部 12 张表，分三组：
--      一、业务表    5 张   项目自己的数据模型，见 model/*.py
--      二、向量表    2 张   langchain-postgres 使用，见 model/vector_model.py
--      三、检查点表  4 张   langgraph-checkpoint-postgres 使用，存对话状态
--  另加 alembic_version（迁移版本表），见文件末尾。
--
--  几点说明：
--    1. 向量表与检查点表平时由对应库在「首次使用」时自动创建，这里显式建出
--       是为了能从零重建一个可用的库；全部加了 IF NOT EXISTS，与自动创建不冲突。
--    2. 公共知识库（knowledge_bases 中 collection_name='public' 的那条）不在此插入，
--       由后端启动时的 lifespan 自动创建，见 config/pgsql/pgsql_connection.py。
--    3. gen_random_uuid() 自 PostgreSQL 13 起是内置函数，不需要 pgcrypto 扩展。
--    4. 脚本整体幂等，可以重复执行。
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================================================
--  一、业务表
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 用户表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            uuid         NOT NULL DEFAULT gen_random_uuid(),
    username      varchar(64)  NOT NULL,
    account       varchar(64)  NOT NULL,
    password      varchar(255) NOT NULL,
    bio           text                  DEFAULT '',
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    is_deleted    boolean      NOT NULL DEFAULT false,
    role          varchar(16)  NOT NULL DEFAULT 'user',
    token_version integer      NOT NULL DEFAULT 0,

    CONSTRAINT users_pkey PRIMARY KEY (id),
    CONSTRAINT users_role_check CHECK (role IN ('admin', 'user'))
);

COMMENT ON TABLE  users               IS '用户表';
COMMENT ON COLUMN users.id            IS '主键';
COMMENT ON COLUMN users.username      IS '账户名/昵称';
COMMENT ON COLUMN users.account       IS '登录账号，全局唯一';
COMMENT ON COLUMN users.password      IS '密码哈希值：md5(lower(明文) + ''salt'')';
COMMENT ON COLUMN users.bio           IS '个人简介';
COMMENT ON COLUMN users.created_at    IS '创建时间';
COMMENT ON COLUMN users.updated_at    IS '更新时间';
COMMENT ON COLUMN users.is_deleted    IS '软删除标记';
COMMENT ON COLUMN users.role          IS '用户角色：admin/user';
COMMENT ON COLUMN users.token_version IS '令牌版本号，登出或改密码时 +1，使此前签发的 token 立即失效';

-- 登录账号只在「未删除」的用户中唯一：软删除后该账号可被重新注册
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_account         ON users (account) WHERE (is_deleted = false);
CREATE INDEX        IF NOT EXISTS idx_users_account_deleted ON users (account, is_deleted);


-- ---------------------------------------------------------------------------
-- 知识库表：一个知识库对应 langchain 里的一个向量集合
--   owner_id 为空 -> 公共知识库，所有人可检索
--   owner_id 非空 -> 私人知识库，只属于该用户
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id              uuid         NOT NULL DEFAULT gen_random_uuid(),
    name            varchar(128) NOT NULL,
    owner_id        uuid,
    collection_name varchar(128) NOT NULL,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    is_deleted      boolean      NOT NULL DEFAULT false,

    CONSTRAINT knowledge_bases_pkey PRIMARY KEY (id),
    CONSTRAINT knowledge_bases_collection_name_key UNIQUE (collection_name),
    CONSTRAINT knowledge_bases_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES users (id)
);

COMMENT ON TABLE  knowledge_bases                 IS '知识库表';
COMMENT ON COLUMN knowledge_bases.id              IS '主键';
COMMENT ON COLUMN knowledge_bases.name            IS '知识库名称';
COMMENT ON COLUMN knowledge_bases.owner_id        IS '拥有者 id，为空则为公共知识库';
COMMENT ON COLUMN knowledge_bases.collection_name IS '对应的向量集合名称（对应 langchain_pg_collection.name）';
COMMENT ON COLUMN knowledge_bases.created_at      IS '创建时间';
COMMENT ON COLUMN knowledge_bases.is_deleted      IS '是否逻辑删除';


-- ---------------------------------------------------------------------------
-- 知识库文件表：文件本体在 MinIO，这张表只记录位置和业务状态
--   状态流转：pending -> approved -> stored / rejected / failed
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_files (
    id                uuid         NOT NULL DEFAULT gen_random_uuid(),
    knowledge_base_id uuid,
    uploader_id       uuid         NOT NULL,
    original_name     varchar(255) NOT NULL,
    object_key        varchar(512) NOT NULL,
    bucket            varchar(64)  NOT NULL DEFAULT 'knowledge',
    content_type      varchar(128),
    size              bigint       NOT NULL,
    file_hash         varchar(64),
    status            varchar(16)  NOT NULL DEFAULT 'pending',
    reviewer_id       uuid,
    review_comment    text,
    reviewed_at       timestamptz,
    error_message     text,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    is_deleted        boolean      NOT NULL DEFAULT false,

    CONSTRAINT knowledge_files_pkey PRIMARY KEY (id),
    CONSTRAINT knowledge_files_object_key_key UNIQUE (object_key),
    CONSTRAINT ck_knowledge_files_status
        CHECK (status IN ('pending', 'approved', 'stored', 'rejected', 'failed')),
    CONSTRAINT knowledge_files_uploader_id_fkey FOREIGN KEY (uploader_id) REFERENCES users (id),
    CONSTRAINT knowledge_files_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES users (id)
    -- 注意：knowledge_base_id 故意不加外键。历史原因见 model/knowledge_model.py 的注释，
    -- 现状与线上库保持一致；若要补，请单独走一次迁移。
);

COMMENT ON TABLE  knowledge_files                   IS '知识库文件表';
COMMENT ON COLUMN knowledge_files.id                IS '主键';
COMMENT ON COLUMN knowledge_files.knowledge_base_id IS '所属知识库 id';
COMMENT ON COLUMN knowledge_files.uploader_id       IS '上传者 id';
COMMENT ON COLUMN knowledge_files.original_name     IS '原始文件名';
COMMENT ON COLUMN knowledge_files.object_key        IS '对象在 MinIO 中的路径，数据库到文件本体的唯一关联';
COMMENT ON COLUMN knowledge_files.bucket            IS '所在存储桶';
COMMENT ON COLUMN knowledge_files.content_type      IS 'MIME 类型';
COMMENT ON COLUMN knowledge_files.size              IS '文件大小，单位字节';
COMMENT ON COLUMN knowledge_files.file_hash         IS '文件内容的 SHA-256，用于去重与识别同名不同内容';
COMMENT ON COLUMN knowledge_files.status            IS '状态：pending/approved/stored/rejected/failed';
COMMENT ON COLUMN knowledge_files.reviewer_id       IS '审核人 id';
COMMENT ON COLUMN knowledge_files.review_comment    IS '审核意见，驳回时填写原因';
COMMENT ON COLUMN knowledge_files.reviewed_at       IS '审核时间';
COMMENT ON COLUMN knowledge_files.error_message     IS '解析失败原因';
COMMENT ON COLUMN knowledge_files.created_at        IS '创建时间';
COMMENT ON COLUMN knowledge_files.updated_at        IS '更新时间';
COMMENT ON COLUMN knowledge_files.is_deleted        IS '是否逻辑删除';


-- ---------------------------------------------------------------------------
-- 会话记录表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id         uuid         NOT NULL DEFAULT gen_random_uuid(),
    user_id    uuid         NOT NULL,
    title      varchar(255)          DEFAULT '',
    created_at timestamptz  NOT NULL DEFAULT now(),
    updated_at timestamptz  NOT NULL DEFAULT now(),
    is_deleted boolean      NOT NULL DEFAULT false,

    CONSTRAINT conversations_pkey PRIMARY KEY (id),
    CONSTRAINT conversations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

COMMENT ON TABLE  conversations            IS '会话记录表';
COMMENT ON COLUMN conversations.id         IS '主键';
COMMENT ON COLUMN conversations.user_id    IS '所属用户';
COMMENT ON COLUMN conversations.title      IS '会话标题';
COMMENT ON COLUMN conversations.created_at IS '创建时间';
COMMENT ON COLUMN conversations.updated_at IS '更新时间';
COMMENT ON COLUMN conversations.is_deleted IS '软删除标记';

CREATE INDEX IF NOT EXISTS idx_conversations_user_id      ON conversations (user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_user_created ON conversations (user_id, created_at DESC);


-- ---------------------------------------------------------------------------
-- 会话消息表：一条会话下的一条消息
--   用户 id 不在这里冗余存储：消息属于会话、会话属于用户，需要时 join 拿
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    conversation_id uuid        NOT NULL,
    role            varchar(16) NOT NULL,
    content         text        NOT NULL,
    sequence        integer     NOT NULL,
    model           varchar(64),
    status          varchar(16) NOT NULL DEFAULT 'done',
    token_count     integer,
    parent_id       uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    is_deleted      boolean     NOT NULL DEFAULT false,

    CONSTRAINT conversation_messages_pkey PRIMARY KEY (id),
    CONSTRAINT uq_messages_conv_seq UNIQUE (conversation_id, sequence),
    CONSTRAINT ck_messages_role   CHECK (role IN ('user', 'assistant', 'system')),
    CONSTRAINT ck_messages_status CHECK (status IN ('pending', 'done', 'error')),
    -- 注意：这个外键没有 ON DELETE CASCADE。删会话时必须「先删消息、再删会话」，
    -- 反过来会直接抛 ForeignKeyViolation。见 main.py 的删除会话接口。
    CONSTRAINT conversation_messages_conversation_id_fkey
        FOREIGN KEY (conversation_id) REFERENCES conversations (id),
    CONSTRAINT conversation_messages_parent_id_fkey
        FOREIGN KEY (parent_id) REFERENCES conversation_messages (id)
);

COMMENT ON TABLE  conversation_messages                 IS '会话消息表';
COMMENT ON COLUMN conversation_messages.id              IS '主键';
COMMENT ON COLUMN conversation_messages.conversation_id IS '所属会话 id';
COMMENT ON COLUMN conversation_messages.role            IS '消息角色：user / assistant / system';
COMMENT ON COLUMN conversation_messages.content         IS '消息内容';
COMMENT ON COLUMN conversation_messages.sequence        IS '会话内序号，从 1 开始递增（created_at 同毫秒会重复，不能靠它排序）';
COMMENT ON COLUMN conversation_messages.model           IS '生成该回复所用的模型，用户消息为空';
COMMENT ON COLUMN conversation_messages.status          IS '消息状态：pending / done / error';
COMMENT ON COLUMN conversation_messages.token_count     IS 'token 数，用于计费与上下文裁剪';
COMMENT ON COLUMN conversation_messages.parent_id       IS '父消息 id，用于「重新生成」的分支';
COMMENT ON COLUMN conversation_messages.created_at      IS '创建时间';
COMMENT ON COLUMN conversation_messages.updated_at      IS '更新时间';
COMMENT ON COLUMN conversation_messages.is_deleted      IS '是否逻辑删除';

-- (conversation_id, sequence) 复合索引：同时覆盖「按会话查」和「按序号排序」
CREATE INDEX IF NOT EXISTS idx_messages_conv_seq ON conversation_messages (conversation_id, sequence);


-- ============================================================================
--  二、向量表（langchain-postgres）
--  正常情况下由 PGVector 在首次写入时自动创建，这里显式建出以便从零重建。
-- ============================================================================

-- 向量集合表：一个 collection 对应一个知识库（公共库名 'public'，私人库名 'user_<user_id>'）
CREATE TABLE IF NOT EXISTS langchain_pg_collection (
    uuid      uuid NOT NULL,
    name      varchar NOT NULL,
    cmetadata json,

    CONSTRAINT langchain_pg_collection_pkey PRIMARY KEY (uuid),
    CONSTRAINT langchain_pg_collection_name_key UNIQUE (name)
);

COMMENT ON TABLE  langchain_pg_collection           IS '向量集合表';
COMMENT ON COLUMN langchain_pg_collection.uuid      IS '主键';
COMMENT ON COLUMN langchain_pg_collection.name      IS '集合名称';
COMMENT ON COLUMN langchain_pg_collection.cmetadata IS '集合级元数据';


-- 向量嵌入表：每个文本段一行，embedding 为 1024 维向量（qwen3-embedding:0.6b）
CREATE TABLE IF NOT EXISTS langchain_pg_embedding (
    id            varchar NOT NULL,
    collection_id uuid,
    embedding     vector(1024),
    document      varchar,
    cmetadata     jsonb,

    CONSTRAINT langchain_pg_embedding_pkey PRIMARY KEY (id),
    CONSTRAINT langchain_pg_embedding_collection_id_fkey
        FOREIGN KEY (collection_id) REFERENCES langchain_pg_collection (uuid) ON DELETE CASCADE
);

COMMENT ON TABLE  langchain_pg_embedding               IS '向量嵌入表';
COMMENT ON COLUMN langchain_pg_embedding.id            IS '主键';
COMMENT ON COLUMN langchain_pg_embedding.collection_id IS '所属集合 id';
COMMENT ON COLUMN langchain_pg_embedding.embedding     IS '1024 维向量';
COMMENT ON COLUMN langchain_pg_embedding.document      IS '文本段内容';
COMMENT ON COLUMN langchain_pg_embedding.cmetadata     IS '文本段元数据：file_id / source / seq / ingested_at / len';

-- 按元数据检索（项目里用 cmetadata->>'file_id' 关联文件、删除文档的分段）
CREATE INDEX IF NOT EXISTS ix_cmetadata_gin ON langchain_pg_embedding USING gin (cmetadata jsonb_path_ops);


-- ============================================================================
--  三、检查点表（langgraph-checkpoint-postgres）
--  对话正文在 checkpoints 系列表里以 thread_id 另存一份，删会话时要一起清。
--  正常情况下由 PostgresSaver.setup() 自动创建。
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id            text  NOT NULL,
    checkpoint_ns        text  NOT NULL DEFAULT '',
    checkpoint_id        text  NOT NULL,
    parent_checkpoint_id text,
    type                 text,
    checkpoint           jsonb NOT NULL,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT checkpoints_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

COMMENT ON TABLE checkpoints IS 'LangGraph 检查点：一次对话状态的快照';

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints (thread_id);


CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id     text  NOT NULL,
    checkpoint_ns text  NOT NULL DEFAULT '',
    channel       text  NOT NULL,
    version       text  NOT NULL,
    type          text  NOT NULL,
    blob          bytea,

    CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

COMMENT ON TABLE checkpoint_blobs IS 'LangGraph 检查点的二进制数据块';

CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs (thread_id);


CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id     text   NOT NULL,
    checkpoint_ns text   NOT NULL DEFAULT '',
    checkpoint_id text   NOT NULL,
    task_id       text   NOT NULL,
    idx           integer NOT NULL,
    channel       text   NOT NULL,
    type          text,
    blob          bytea  NOT NULL,
    task_path     text   NOT NULL DEFAULT '',

    CONSTRAINT checkpoint_writes_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

COMMENT ON TABLE checkpoint_writes IS 'LangGraph 检查点的中间写入记录';

CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes (thread_id);


CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v integer NOT NULL,

    CONSTRAINT checkpoint_migrations_pkey PRIMARY KEY (v)
);

COMMENT ON TABLE checkpoint_migrations IS 'LangGraph 已执行的迁移版本';

-- langgraph 启动时会检查这张表：缺行就会重跑建表迁移，而表已存在 -> 报错。
-- 下面 0~9 对应 langgraph-checkpoint-postgres 3.1.2，升级该库时需同步调整。
INSERT INTO checkpoint_migrations (v) VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)
ON CONFLICT DO NOTHING;


-- ============================================================================
--  四、迁移版本表（alembic 使用）
-- ============================================================================

CREATE TABLE IF NOT EXISTS alembic_version (
    version_num varchar(32) NOT NULL,

    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

COMMENT ON TABLE alembic_version IS 'Alembic 迁移版本，由 alembic 自行维护';

-- 表结构由本脚本创建时，可把版本号标到最新，避免 alembic 重复执行建表迁移。
-- 版本号对应 alembic/versions 下的脚本文件名前缀，迁移有增减时需要改。
-- INSERT INTO alembic_version (version_num) VALUES ('467959c28912') ON CONFLICT DO NOTHING;
