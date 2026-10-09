# 智语 AI · 企业知识库对话助手

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.140-009688?logo=fastapi&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1.3-1C3C3C?logo=langchain&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C?logo=langgraph&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![pgvector](https://img.shields.io/badge/pgvector-0.8-4169E1?logo=postgresql&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3.3-4FC08D?logo=vuedotjs&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-4.4-646CFF?logo=vite&logoColor=white)
![Element Plus](https://img.shields.io/badge/Element_Plus-2.4-409EFF?logo=element&logoColor=white)
![MinIO](https://img.shields.io/badge/MinIO-Storage-C72C48?logo=minio&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-Embedding-000000?logo=ollama&logoColor=white)

把企业文档变成可对话的知识库：上传 → 解析切分 → 向量化入库 → 检索增强问答，答案附带原文出处。

- **RAG 问答**：回答只依据检索到的语料，未命中时明确拒答，不编造
- **双轨知识库**：私人库（本人上传即入库）与公共库（管理员上传、审核后入库）分离管理
- **逐段可追溯**：每个文本段带来源文档、序号、入库时间，问答结果可回溯到原文
- **管理员审核台**：公共库导入走「待审核 → 通过 → 已入库」流程，审核前可直接预览文档正文



---

## 目录

- [界面预览](#界面预览)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [默认账号](#默认账号)
- [环境变量](#环境变量)
- [数据模型](#数据模型)
- [接口一览](#接口一览)
- [已知限制](#已知限制)

---

## 界面预览

### 登录 / 注册

![image-20261009150026141](assets/image-20261009150026141.png)

### AI 对话

![image-20261009150048159](assets/image-20261009150048159.png)

### 我的知识库

![image-20261009150105680](assets/image-20261009150105680.png)

### 导入审核（管理员）

![image-20261009150115996](assets/image-20261009150115996.png)

![image-20261009150143400](assets/image-20261009150143400.png)

### 知识库语料

![image-20261009150159247](assets/image-20261009150159247.png)

![image-20261009150235487](assets/image-20261009150235487.png)

---

## 技术栈

### 后端

| 组件 | 选型 | 用途 |
|---|---|---|
| Web 框架 | FastAPI 0.140 + uvicorn | 接口与流式响应 |
| ORM | SQLAlchemy 2.0（asyncio）+ asyncpg | 异步数据库访问 |
| 数据库 | PostgreSQL 17 + pgvector 0.8 | 业务数据与向量检索 |
| 迁移 | Alembic 1.18 | 表结构版本管理 |
| 对象存储 | MinIO | 原始文件本体 |
| 对话编排 | LangGraph 1.2 + langgraph-checkpoint-postgres | 多轮对话与状态持久化 |
| 向量层 | langchain-postgres 0.0.18 | 集合与向量读写 |
| 文档解析 | langchain-community / unstructured / pypdf | PDF / Word / Markdown / TXT |
| 模型接入 | langchain-deepseek / langchain-openai / langchain-ollama | 对话模型与向量模型 |
| 鉴权 | PyJWT | 无状态 Token |

### 前端

| 组件 | 选型 | 用途 |
|---|---|---|
| 框架 | Vue 3（`<script setup>`） | 页面与组件 |
| 构建 | Vite 4 | 开发服务器与打包 |
| UI | Element Plus 2 | 表格、表单、抽屉等 |
| 路由 | vue-router 4（hash 模式） | 页面路由与登录守卫 |
| Markdown | marked + DOMPurify | AI 回复渲染（必过 XSS 过滤） |

---

## 系统架构

```
                          ┌──────────────────────────┐
   浏览器 ───────────────► │  前端  Vue 3 + Vite      │
   Bearer Token           │  开发端口 5173           │
                          └────────────┬─────────────┘
                                       │ /api/* 由 Vite 代理转发（去掉 /api 前缀）
                                       ▼
                          ┌──────────────────────────┐
                          │  后端  FastAPI  :8000    │
                          ├──────────────────────────┤
   鉴权 auth.py ─────────►│ PostgreSQL               │ users
                          │                          │
   对话 agent/ ──────────►│ LangGraph checkpoint 表  │ checkpoints / blobs / writes
        │                 │                          │
        └── 检索 ────────►│ pgvector                 │ langchain_pg_embedding
                          │                          │
   知识库 knowledge.py ──►│ MinIO  桶 knowledge      │ 原始文件
        │                 │ PostgreSQL               │ knowledge_files / knowledge_bases
        │                 │                          │
        └── 向量化 ──────►│ Ollama  qwen3-embedding  │ 1024 维
                          └──────────────────────────┘
```

**两条独立的检索路径**，改权限时别混淆：

- **问答检索**（全员可用）：`agent/` 内部按知识库集合逐路检索，私人库 + 公共库都查，与下面的浏览接口无关
- **语料浏览**（受权限约束）：`GET /knowledge/vector/list`、`GET /knowledge/file/list`，公共库语料仅管理员可浏览原文

---

## 目录结构

```
ai_assistant_project/
├── ai_assistant_backend/              # 后端
│   ├── main.py                        # 应用入口：鉴权、会话、对话（含流式）
│   ├── auth.py                        # JWT 签发与校验
│   ├── knowledge.py                   # 知识库路由：上传 / 审核 / 语料 / 预览 / 删除 / 统计
│   ├── agent/                         # LangGraph 对话代理与模型清单
│   ├── rag/loader.py                  # 文档加载与切分
│   ├── model/                         # SQLAlchemy ORM 模型
│   ├── config/
│   │   ├── pgsql/                     # 连接、会话、pgvector 客户端、lifespan
│   │   └── minio/                     # MinIO 客户端与桶
│   ├── alembic/                       # 数据库迁移脚本
│   ├── sql/init.sql                   # ★ 数据库初始化脚本（12 张表）
│   ├── .env.example                   # 环境变量模板；.env 不入库
│   └── requirements.txt
│
└── ai_assistant_frontend/             # 前端
    ├── src/api/                       # 唯一接口层（含后端字段 → 视图字段的映射）
    ├── src/pages/                     # 登录 / 对话 / 我的知识库 / 导入审核 / 知识库语料
    ├── src/components/                # 导航栏、品牌标识、Markdown 渲染等
    ├── src/styles/                    # 设计变量与全局样式
    └── vite.config.js                 # 开发代理：/api → http://127.0.0.1:8000
```

---

## 快速开始

### 1. 准备依赖服务

| 服务 | 说明 | 默认地址 |
|---|---|---|
| PostgreSQL + pgvector | 业务数据 + 向量库 | `192.168.100.5:5432` |
| MinIO | 原始文件对象存储 | `192.168.100.5:9000` |
| Ollama | 向量模型 `qwen3-embedding:0.6b` | `localhost:11434` |
| 大模型 API | 至少配置一家（DeepSeek / 阿里云百炼 / MiMo） | —— |

Ollama 拉取向量模型：

```bash
ollama pull qwen3-embedding:0.6b
```

MinIO 需要存在存储桶 `knowledge`（后端上传时会写入，桶需提前创建）。

### 2. 初始化数据库

```bash
createdb zhiyu_ai
psql -d zhiyu_ai -f ai_assistant_backend/sql/init.sql
```

脚本会建出全部 12 张表（业务表 5 + 向量表 2 + 检查点表 4 + 迁移版本表 1），
并自动创建 `vector` 扩展。脚本幂等，可以重复执行。

> 公共知识库那条记录**不需要手动插入**：后端启动时会自动检查并创建。

如果这个库交给 Alembic 管理，也可以跳过本脚本改用迁移（迁移脚本在 `alembic/versions/`，
连接串从 `.env` 的 `DB_URL` 读取）：

```bash
cd ai_assistant_backend
.venv/Scripts/python.exe -m pip install alembic     # 当前虚拟环境里没有装
.venv/Scripts/alembic.exe upgrade head
```

> 注意不要写成 `python -m alembic`：后端根目录下有个同名的 `alembic/` 目录，
> Python 会把它当成命名空间包导入，报出的错误与真实原因无关。

### 3. 配置后端环境变量

**`.env` 不入库**，仓库里只提供模板 `.env.example`（键名齐全、值是空的）。
复制一份再填真实值：

```bash
cd ai_assistant_backend
cp .env.example .env        # Windows: copy .env.example .env
```

至少要填这三项，其余按需（不用的模型留空即可）：

```dotenv
DB_URL=postgresql://<用户>:<密码>@<主机>:5432/zhiyu_ai
JWT_SECRET_KEY=<随机字符串，生成方式见下>
DEEPSEEK_API_KEY=<你的 Key>
```

生成一个 JWT 密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

完整键名与说明见[环境变量](#环境变量)。

### 4. 启动后端

```bash
cd ai_assistant_backend

# 首次：创建虚拟环境并安装依赖（Python 3.14+）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 启动（工作目录必须是后端根目录）
.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

接口文档：<http://127.0.0.1:8000/docs>

### 5. 启动前端

```bash
cd ai_assistant_frontend
npm install
npm run dev
```

打开 <http://localhost:5173>。前端统一请求 `/api/*`，由 Vite 代理转发到后端，
浏览器侧是同源请求，不依赖后端 CORS。

后端不在默认端口时，用环境变量指定：

```bash
VITE_BACKEND_ORIGIN=http://127.0.0.1:8001 npm run dev
```

---

## 默认账号

| 账号 | 密码 | 角色 | 说明 |
|---|---|---|---|
| `admin` | `123456` | 管理员 | 可上传 / 审核公共知识库，可浏览全部语料 |
| `test01` | `123456` | 普通用户 | 只能看到自己的私人知识库 |

> 演示环境使用弱口令，正式部署前请务必修改。

---

## 环境变量

后端配置位于 `ai_assistant_backend/.env`。**该文件不入库** —— 已在 `.gitignore` 中，
仓库里只提交模板 `.env.example`，克隆下来后复制一份再填真实值（见[快速开始](#快速开始)第 3 步）。

| 变量 | 必填 | 说明 |
|---|---|---|
| `DB_URL` | 是 | PostgreSQL 连接串，形如 `postgresql://user:pwd@host:5432/dbname` |
| `JWT_SECRET_KEY` | 是 | JWT 签名密钥，泄露等同于任何人都能伪造任意用户的登录态，换环境时重新生成 |
| `CORS_ORIGINS` | 否 | 跨域白名单，逗号分隔；`*` 表示全部放行（默认）。鉴权走 `Authorization` 头而非 Cookie，所以用 `*` 是安全的；生产环境建议收敛为具体域名 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL_NAME` | 否 | DeepSeek 模型接入 |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` | 否 | 阿里云百炼模型接入 |
| `MIMO_API_KEY` / `MIMO_BASE_URL` / `MIMO_MODEL_NAME` | 否 | MiMo 模型接入 |
| `TAVILY_API_KEY` | 否 | Web 搜索工具 |

可用的对话模型清单由 `GET /model/get` 返回，前端不硬编码。

> 新增配置项时请同步更新 `.env.example`，保持两边键名一致 —— 否则新克隆仓库的人会缺项。

---

## 数据模型

全部 12 张表由 `ai_assistant_backend/sql/init.sql` 创建，分三组：

### 业务表（5）

| 表 | 说明 |
|---|---|
| `users` | 用户表。`role` 区分管理员/普通用户；`token_version` 在登出或改密码时 +1，使已签发的 Token 立即失效 |
| `knowledge_bases` | 知识库表。`owner_id` 为空即公共库；`collection_name` 关联向量集合（公共库为 `public`，私人库为 `user_<user_id>`） |
| `knowledge_files` | 文件元数据。文件本体在 MinIO，`object_key` 是两者的唯一关联；状态流转 `pending → approved → stored / rejected / failed` |
| `conversations` | 会话记录。`user_id` 外键带 `ON DELETE CASCADE` |
| `conversation_messages` | 会话消息。`(conversation_id, sequence)` 唯一，序号从 1 递增（`created_at` 同毫秒会重复，不能靠它排序） |

### 向量表（2，langchain-postgres）

| 表 | 说明 |
|---|---|
| `langchain_pg_collection` | 向量集合，一个集合对应一个知识库 |
| `langchain_pg_embedding` | 文本段与向量。`embedding` 为 `vector(1024)`；`cmetadata` 存 `file_id / source / seq / ingested_at / len`，按 `file_id` 关联回文件 |

### 检查点表（4，langgraph-checkpoint-postgres）

`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations` —— 对话正文在这里按 `thread_id` 另存一份，删除会话时需要一并清理。

### 迁移版本表（1）

`alembic_version`，由 Alembic 维护。

> 这几张由第三方库自动建的表（向量表、检查点表）平时不需要手动创建，
> `init.sql` 里显式写出是为了能从零重建一个可用的库。

---

## 接口一览

共 21 个接口，鉴权统一使用 `Authorization: Bearer <token>`。

### 用户与鉴权

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/users/register` | 注册，成功后自动创建私人知识库 |
| POST | `/users/login` | 登录，返回 Token 与用户信息 |
| GET | `/users/me` | 校验当前登录态并返回用户信息 |
| POST | `/users/logout` | 登出，Token 版本 +1 |

### 会话与对话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/conversation/add` | 新建会话 |
| GET | `/conversation/list` | 会话列表（按创建时间倒序） |
| GET | `/conversation/messages/list` | 会话消息（按序号升序，含归属校验） |
| DELETE | `/conversation/{conversation_id}` | 删除会话（消息 + 检查点一并清理） |
| POST | `/conversation/chat` | 对话（一次性返回） |
| POST | `/conversation/chat/stream` | 对话（流式返回） |

### 知识库

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/knowledge/admin/upload` | 管理员 | 上传到公共库，落为「待审核」 |
| POST | `/knowledge/user/upload` | 登录用户 | 上传到私人库，上传即解析入库；同名可带 `replace` 替换 |
| GET | `/knowledge/file/conflict` | 登录用户 | 上传前探测同名冲突 |
| GET | `/knowledge/file/list` | 见说明 | 文件列表（服务端分页）；公共库仅管理员 |
| GET | `/knowledge/file/{id}/preview` | 见说明 | 预览文档正文（只解析，不写向量库） |
| GET | `/knowledge/file/{id}/download` | 见说明 | 下载原文（后端转发对象存储） |
| DELETE | `/knowledge/file/{id}` | 见说明 | 删除文档：向量分段 + 记录 + 对象三处一起清 |
| POST | `/knowledge/review/{id}` | 管理员 | 审核通过 / 驳回 |
| GET | `/knowledge/vector/list` | 见说明 | 语料分段列表；公共库仅管理员 |
| GET | `/knowledge/tags` | 登录用户 | 汇总统计（管理员含全库与公共库口径） |

> 「见说明」的接口：公共库资源仅管理员可访问；私人库资源仅库主本人（或管理员）。
> 「不存在 / 已删除 / 无权限」统一返回 404，避免被用来探测 id 是否存在。
> **权限收紧只影响「浏览原文」，不影响问答检索** —— RAG 走内部检索路径。

### 其它

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/model/get` | 可用对话模型清单 |

---

## 已知限制

- **密码哈希强度不足**：当前为 `md5(明文转小写 + 固定盐)`。仅适用于演示环境，正式上线必须换成 bcrypt / argon2。
- **无回收站**：删除文件是软删除且界面无找回入口，管理端暂无「查看已删除」功能。
- **私人知识库每人限 1 个**：注册时自动创建，不支持自定义名称或创建多个。
- **`knowledge_files.knowledge_base_id` 未加外键**：历史原因保留现状，需要时单独走一次迁移补上。
- **`conversation_messages` 的外键没有 `ON DELETE CASCADE`**：删除会话时必须「先删消息、再删会话」，顺序反了会触发外键约束错误。
- **系统配置在前端硬编码**：存储桶名、向量模型与维度、上传限制等目前写在前端常量里，后端尚未提供配置下发接口。
- **未建向量索引**：当前数据量下走顺序扫描即可，数据量上来后需要补 HNSW / IVFFlat 索引。
