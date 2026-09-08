# Terpene Atlas — IGEM 代谢通路数据库

NJU-CHINA 参加 IGEM 比赛的专用数据库。以萜类合酶及相关化合物为核心，
构建"酶-反应-化合物"图结构，支持可交互通路浏览、条目/通路检索、同源检索和数据下载。

---

## 环境要求

- **Python 3.11+**（含 pip）
- **MySQL 8.0+**
- **Node.js 18+**（前端用）

---

## 快速开始

```bash
# 1. 克隆仓库
git clone <repo-url>
cd igem_database

# 2. 创建 MySQL 数据库和表
mysql -u root -p < sql/schema.sql

# 3. 创建 Python 虚拟环境
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS/Linux

# 4. 安装 ETL 依赖并导入数据
pip install -r etl/requirements.txt
set IGEM_DB_PASSWORD=你的密码            # Windows
# export IGEM_DB_PASSWORD=你的密码        # macOS/Linux
python etl/etl_run.py

# 5. 启动后端
pip install -r backend/requirements.txt
set IGEM_DB_PASSWORD=你的密码
cd backend
uvicorn app.main:app --reload --port 8000

# 6. 启动前端（另开一个终端）
cd frontend
npm install
npm run dev
```

启动后访问 **http://localhost:5173** 查看前端页面，**http://localhost:8000/docs** 查看 API 文档。

---

## 一键启动

仓库根目录提供了 `start_igem.sh`，会自动完成：

1. 写入后端环境配置
2. 自动安装并启动 MySQL（需要 `sudo`）
3. 创建数据库和表
4. 安装 Python / 前端依赖
5. 执行 ETL 导入
6. 启动后端和前端

运行前先准备好 MySQL，然后直接执行：

```bash
bash start_igem.sh
```

如需自定义数据库账号，可以先设置 `IGEM_DB_PASSWORD`，也可以在脚本运行时输入密码。

---

## 项目结构

```
igem_database/
├── sql/schema.sql              # MySQL 建表脚本（核心表 + 原始数据索引表）
├── database 1st data/          # 原始 TSV 数据（UniProt、Rhea、ChEBI）
├── etl/                        # ETL 脚本：TSV → MySQL
│   ├── config.py               # 数据库连接配置
│   ├── etl_run.py              # 一键执行所有步骤
│   └── etl_*.py                # 各表导入脚本
├── backend/                    # FastAPI 后端（Python）
│   ├── app/
│   │   ├── main.py             # 应用入口 + 路由注册
│   │   ├── config.py           # 配置（数据库、BLAST）
│   │   ├── database.py         # SQLAlchemy 异步引擎
│   │   ├── models/             # ORM 模型
│   │   ├── schemas/            # Pydantic DTO
│   │   ├── routers/            # API 路由（/api/v1/...）
│   │   ├── services/           # 业务逻辑
│   │   └── utils/              # 工具函数
│   ├── requirements.txt
│   └── .env                    # 数据库密码（不提交到 git）
├── frontend/                   # React + Vite 前端
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── 代谢通路数据库搭建需求整理.docx            # 需求文档
└── 代谢通路数据库前后端接口与字段说明.docx       # 接口与字段说明
```

---

## 环境变量

| 变量                | 默认值         | 说明              |
|---------------------|---------------|-------------------|
| `IGEM_DB_HOST`      | `localhost`   | MySQL 主机地址     |
| `IGEM_DB_PORT`      | `3306`        | MySQL 端口        |
| `IGEM_DB_USER`      | `root`        | MySQL 用户名       |
| `IGEM_DB_PASSWORD`  | *(必填)*      | MySQL 密码         |
| `IGEM_DB_NAME`      | `igem_terpene`| 数据库名           |
| `IGEM_BLAST_BIN`    | `blastp`      | BLAST 可执行文件路径 |

运行 ETL 或后端前必须设置这些变量。后端可通过 `backend/.env` 文件配置。

---

## MySQL 数据表

| 表名                   | 行数    | 说明                          |
|------------------------|--------|-------------------------------|
| `compound`             | ~600+  | 化合物节点（ChEBI，过滤通用小分子） |
| `enzyme`               | ~996   | 酶静态属性                     |
| `gene`                 | ~1000+ | 酶的基因链接                   |
| `gene_sequence_link`   | ~1000+ | INSDC/RefSeq 外部序列链接      |
| `reaction`             | ~628   | 反应事实（Rhea）               |
| `reaction_compound`    | ~2700  | 底物/产物关系                  |
| `enzyme_reaction_edge` | ~2400  | 图中边（酶 ↔ 反应）            |
| `evidence`             | ~4000+ | 文献证据（DOI/PubMed/题名/期刊等） |
| `enzyme_go`            | ~2600+ | GO 注释                        |
| `enzyme_isoform`       | ~50+   | 同工型/可变 isoform 序列        |
| `search_index`         | 动态    | 聚合所有 TSV 字段的后端检索索引 |
| `pathway_cache`        | 空     | 通路结果缓存                   |

---

## API 接口（13 个）

基础路径：`/api/v1`

| 方法   | 路径                                 | 说明           |
|--------|--------------------------------------|----------------|
| GET    | `/metadata/filter-options`           | 获取筛选项      |
| GET    | `/graph`                             | 网络图数据       |
| GET    | `/graph/edge-groups/{id}/edges`      | 展开重叠边       |
| GET    | `/search/entries`                    | 条目检索         |
| POST   | `/search/pathways`                   | 通路检索         |
| GET    | `/enzymes/{enzymeId}`               | 酶详情页         |
| GET    | `/compounds/{compoundId}/card`      | 化合物卡片       |
| GET    | `/reactions/{reactionId}`           | 反应详情         |
| POST   | `/homology/search`                   | 发起同源检索     |
| GET    | `/homology/jobs/{jobId}`            | 查询同源检索状态  |
| POST   | `/download/preview`                  | 下载预览         |
| POST   | `/download/files`                    | 生成下载文件     |
| GET    | `/assets/reactions/{rheaId}/atom-map.svg`  | 反应原子图 SVG  |
| GET    | `/assets/compounds/{chebiId}/structure.svg` | 化合物结构图 SVG |
