# 数据库协议（精简版）

本页说明这个萜类酶数据库**怎么建的、里面有什么、怎么跑起来**，以及界面上那个溶解度分数
**到底是什么意思**。目的是让第三方 —— 评委，或者后来的队伍 —— 能自己判断数据是否可信、
整条建库流程能否复现。

> 这是精简版：砍掉推理过程，只留结论、判据和命令。英文版
> [`database-protocol.md`](database-protocol.md) 仍是完整版，尚未按本版风格重写。

两条贯穿全文的约定：

- **文中路径都相对仓库根目录。** §14 的命令除非特别说明，一律在仓库根目录执行。
- **尖括号里的都是占位符。** `<数据盘>` 的意思是「你自己选一块盘」。我们不规定目录位置，
  只规定它**不能落在哪里**（§4）。

规模与行数都是**实测快照**，会随 UniProt 更新而变（最近一次实测 2026-09-20）。
稳定的部分是结构性的那些：表结构、流程、依赖。

---

## 1. 项目总览

**定位。** 这是 NJU-CHINA 参加 iGEM 比赛的配套数据库。它以**萜类合酶（terpene synthase）
及相关化合物**为核心，把分散在 UniProt、Rhea、ChEBI 等公共数据源里的信息整理成一张
**「酶–反应–化合物」图**，并提供一个只读的 Web 应用，支持可交互通路浏览、条目与通路检索、
同源检索（BLAST）和数据下载。

数据规模（2026-09-20 实测）：**95,869 个酶**（1,535 Swiss-Prot + 94,334 TrEMBL）、
714 个反应、734 个化合物、20,616 条酶–反应边、3,756,596 行检索索引。

**组成。** 整个项目是**四段式流水线 + 一个只读 Web 应用**。段与段之间不通过代码耦合，
只通过磁盘上的 TSV 文件交接。

- **① 采集加工** —— `update_tool/`：调外部 API，产出 `for_*/` 下按来源分段的 TSV。可重跑；联网步骤约 3 小时。
- **② 灌库** —— `etl/`：TSV → MySQL 15 张表。可重跑，幂等，约 41 分钟。
- **③ 后端** —— `backend/`：只读 FastAPI + BLAST。无状态。
- **④ 前端** —— `frontend/`：图谱与检索界面。无状态。

四段之外还有三样东西，各自独立成篇：

- **交接数据** `for_*/` —— ①与②之间唯一的契约，也是 ETL 唯一的输入（§7、§8）。
- **建表语句** `sql/schema.sql` —— DDL 的唯一真相源，灌库程序运行时从它抽取各条 `CREATE TABLE`（§9）。
- **文档与许可** —— [`README.md`](../../README.md) 是运维手册（怎么装、怎么跑、出错了怎么办），
  本页是协议（凭什么信、能不能复现）；许可见 §16。

**边界。** 三处容易被误会的地方，提前说清：结构检索是 InChIKey **精确匹配**，不是子结构或
相似性搜索（§10）；界面上那个溶解度数字是**模型参考分**，不是可溶性标签、也不是校准过的
概率（§13）；本页的规模数字是快照，会随 UniProt 更新而漂移（§15）。

## 2. 项目架构与技术栈

### 2.1 架构

**数据流是单向的。** 外部数据源 → ① 采集加工 → `for_*/` 下的 TSV → ② 灌库 → MySQL →
③ 只读 API → ④ 单页应用。四段之间**没有任何代码耦合**，唯一的交接面是磁盘上的 TSV 文件。

这样切的目的是把「取数」与「灌库」分开重跑：联网那步慢且容易断（TrEMBL 段 2.5–3 小时），
把它同本地那步隔开，重试的代价才小。代价是磁盘上多留一份中间产物。

**唯一的契约是 `for_*/`。** ETL 只从 `etl/sources.py` 一个入口读它们，文件不齐时**直接报错
退出**，绝不静默跳过。理由是风险不对称：多读一个来源只是白费时间，少读一个来源是**静默丢
数据**，而这种缺陷的表征恰恰是「一切看起来都正常」。

**后端是分层的。** `router`（HTTP 与参数校验）→ `service`（业务逻辑）→ `model`（ORM）。
出参统一包成 `{success, data, meta?, error?}` 信封；错误用 `SCREAMING_SNAKE_CASE` 错误码，
HTTP 一律 200，靠 `success: false` 表达失败，而不是靠 HTTP 状态码。

**运行时拓扑。**

- **后端自身无状态** —— 但**并非只有 MySQL 与 BLAST+ 两个外部依赖**：结构图那两个端点会实时
  转发到 `rhea-db.org` 与 `ebi.ac.uk`，只带一层进程内缓存（§5、§12.3）。
- **前端无状态** —— 构建成静态 `dist/`；请求一律走相对路径 `/api/v1`，由反向代理转给后端（§10）。
- **数据只读** —— Web 应用不改数据库。`pathway_cache` 是唯一一张为运行期准备的表，
  但**目前没有任何代码读写它**（§9）。

### 2.2 技术栈

- **① 采集加工** —— Python 3.10+。`requests` 调外部 API（6 个脚本用到）、`rdkit` 现算派生的
  InChIKey；其余是标准库（`csv` / `json` / `gzip` / `urllib` / `hashlib`）。
- **② 灌库** —— Python。`pandas` 读 TSV、`SQLAlchemy` + `pymysql` 同步写库、
  `requests` 补 UniProt 字段。
- **③ 后端** —— Python 3.10+。`FastAPI` + `uvicorn[standard]`；`SQLAlchemy[asyncio]` +
  `aiomysql` 异步访问 MySQL；`pydantic` v2 + `pydantic-settings` 管配置；`httpx`、`openpyxl`
  （导出 xlsx）、`rdkit`（SMILES → InChIKey）。
- **④ 前端** —— TypeScript + React + Vite。图标用 `lucide-react`；**图谱是自己写的 SVG，
  没有引入 d3 之类的图库**。结构式编辑器是随仓库分发的 Ketcher 预构建包（Indigo / WASM）。
- **数据库** —— MySQL 8.0+ / InnoDB / utf8mb4。
- **外部程序** —— NCBI BLAST+ 以子进程方式调用，**不在仓库内**（§5）。

**版本口径。** 三份 Python 依赖清单都写下界（`>=`），是安装声明的来源。前端 `package.json`
里一律写 `latest`，但仓库里有 `package-lock.json` 锁住实际版本 —— **用 `npm install`
（它读 lockfile），不要用 `npm update`**，否则会拿到一份与实际实测不符的版本。

### 2.3 数据库架构

**三个根实体、两张边表、六张附属表、一张检索索引、三张独立表。** 库是**以酶为中心**的：
15 张表共 11 条外键，其中 8 条指向 `enzyme`。**没有一条外键带级联**（`ON DELETE` / `ON UPDATE`
一条都没有）—— 所以清理时必须子表优先（§8）。

- **根实体（3 张，无外键）** —— `enzyme`、`compound`、`reaction`。
- **边表（2 张）** —— `enzyme_reaction_edge`（酶催化反应，**图谱的本体**）与
  `reaction_compound`（化合物参与反应，带 `role` 区分底物 / 产物）。两张都是多对多的连接表 ——
  界面上那张「酶–反应–化合物」图，在库里就是它们 join 出来的。
- **附属表（6 张，外键全部指向 `enzyme`）** —— `gene`、`gene_sequence_link`、`evidence`、
  `enzyme_go`、`enzyme_isoform`、`enzyme_solubility_score`。
- **检索索引（1 张）** —— `search_index`，外键也指向 `enzyme`，但行数比酶表大两个数量级
  （3,756,596 行，占库约 75%），所以单独看。
- **独立表（3 张，无外键）** —— `enzyme_id_map` 与 `enzyme_alias_map` 是编号稳定性表（§15）；
  `pathway_cache` 目前没有任何代码读写它。

**为什么「一酶多值」拆成子表、不并进 `enzyme`。** 这些列是列族（多个核酸编号、多个 GO、多篇
文献），列数随数据增长。程序按列头推导列宽，而非写死上限 —— 写死上限的后果不是报错，而是
超出的部分**不存在**（§15）。

**编号映射表刻意不加外键**（`sql/schema.sql` 里留了注释说明）。理由：`enzyme_id_map` 记录每个
UniProt 登录号**首次**获得的编号，它必须在 `enzyme` 的行被清掉之后依然留着；一旦加了外键，
清 `enzyme` 时要么被挡住、要么（若带级联）把编号一并删掉，编号稳定性就没有了。这也是它永不
参与 `DELETE` / `TRUNCATE` 的原因（§15）。

表的完整清单与必需的索引见 §9。

### 2.4 四段流水线

仓库里的东西分三类：**流水线与应用**（跑数据库需要的）、**交接数据**（①与②之间的产物，
也是仓库里体积最大的部分）、**开发期产物**（建库时用的，跑任何东西都不需要）。下面
2.3–2.5 逐类展开。

**① 采集加工 `update_tool/`** —— 20 个脚本，按角色分六类：

- **驱动入口（3 个）** —— `run_all.py` 是唯一正常入口，按 `--source=<s>` 或 `--merge` 驱动下面
  这些脚本；`update_database.py` 把产物部署进 `for_*/`（带 `--dry-run`）；`source_registry.py`
  是来源的唯一登记处，加来源只改这里。
- **下载（1 个）** —— `download_uniprot.py`：取回 UniProt 原始导出，并在本地按 `Reviewed` 拆段。
- **抓取（5 个）** —— `fetch_rhea`、`fetch_go`、`fetch_isoform`、`fetch_references`、
  `fetch_sequence_links`：各自连一个外部接口，补本地导出里没有的内容。
- **加工（7 个）** —— `build_all_nodes`、`build_enzyme_merged`、`build_names_split`、
  `build_rhea_summary`、`build_terpene_compounds`、`build_terpene_only`、`build_terpene_pairs`：
  把下载与抓取的结果拼成 `for_*/` 里的表。
- **解析与重组（3 个）** —— `parse_names`、`protein_name`、`rebuild_master`。
- **其他（1 个）** —— `update_chebi_library.py`：下载并整理 ChEBI 平面文件（§7）。

> `update_tool/` 里的脚本**必须经 `run_all.py` 驱动**：它们的默认输入路径指向从未随仓库分发的
> 文件名，只有 `run_all.py` 会传真实路径（§15）。

**② 灌库 `etl/`** —— 11 个脚本：

- `etl_run.py` —— 唯一入口，按固定顺序调用下面 6 个阶段模块，前后另有维护动作（§8）。
- **6 个阶段模块** —— `etl_compounds`、`etl_enzymes`、`etl_reactions`、`etl_edges`、
  `etl_master`、`etl_search_index`。
- **4 个支撑** —— `sources.py`（读 `for_*/` 的唯一入口）、`config.py`（只读环境变量）、
  `db_utils.py`（upsert 等）、`cell_values.py`。

**③ 后端 `backend/app/`** —— 只读 FastAPI 服务，分五层：

- **装配层** —— `main.py`、`config.py`、`database.py`、`deps.py`。
- **11 个 router** —— `metadata`、`graph`、`search`、`structure_search`、`enzymes`、
  `compounds`、`reactions`、`blast`、`download`、`assets`、`bundle`。所有接口在 `/api/v1` 下。
- **6 个 service** —— `blast`、`download`、`entity`、`graph`、`pathway`、`search`：业务逻辑在这一层。
- **13 个 model** —— 对应 §9 的表。**15 张表里有两张没有 ORM 模型** —— `enzyme_id_map` 与
  `enzyme_alias_map`。
- **13 个 schema** —— 出参结构，统一包在 `{success, data, meta?, error?}` 信封里。
- **5 个 util** —— `chemistry`（RDKit 把 SMILES 转成 InChIKey）、`compound_card`、
  `compound_filters`、`errors`、`query_parser`。

**④ 前端 `frontend/src/`** —— React + Vite 单页应用：

- **3 个页面** —— `HomePage`（首页图谱）、`SearchResultsPage`（检索结果，含 BLAST 模式）、
  `DownloadsPage`（下载表）。
- **3 个抽屉** —— `BlastDrawer`、`SearchSetControl`、`StructureSearchDrawer`。
- `lib/` 与 `styles/` —— 请求封装、图谱与布局逻辑、样式。

### 2.5 交接数据 `for_*/`

这四个目录是 ①与②之间**唯一的契约**，也是 ETL 唯一的输入。每个目录里，带 `.<来源>.tsv`
后缀的是按来源分段的文件（`swiss_prot` / `trembl`），不带后缀的是两段的合并表。

- **`for_enzyme_detail/`** —— `uniprotkb_master.{swiss_prot,trembl,}.tsv`，以及 `child_tables/`
  下的七张子表，每张三个文件：`go`、`isoform_sequences`、`names_split`、`references`、`rhea`、
  `sequence_links`、`solubility_score`。
- **`for_enzyme_reaction_card/`** —— `uniprotkb_enzyme_merged.*`、`uniprotkb_rhea_summary.*`。
- **`for_compound_card/`** —— `uniprotkb_terpene_compounds.tsv`。
- **`for_graph/`** —— `all_nodes.tsv`、`uniprotkb_terpene_only.*`、`uniprotkb_terpene_pairs.tsv`。

**有三张表只有合并版、没有分段版** —— `all_nodes.tsv`、`uniprotkb_terpene_pairs.tsv`、
`uniprotkb_terpene_compounds.tsv`。它们**不能按来源切开**：一对底物→产物天然由两个来源的酶共享。

### 2.6 开发期产物

建库期间我们在仓库根目录留了一组随手写的探针脚本和它们产出的测量数据。**跑流水线或 Web 应用
都不需要**，也不计入 §3 的磁盘数字。这些脚本已于 2026-10-02 删除，本节是留档。

把它们挡在仓库外的规则（`/_*.py`、`/_*.ps1`、`/_*.log`，以及五条 JSON 模式）仍在 `.gitignore`
里。这些规则**故意锚定在根目录**：裸 `_*.json` 不认目录层级，会伸进子目录，把被跟踪的真实数据
一起误伤。

## 3. 环境要求

- **Python** 3.10+（我们的构建环境实测 3.11）
- **MySQL** 8.0+，InnoDB，字符集统一 utf8mb4
- **Node.js** ≥ 20（只有前端需要）
- **NCBI BLAST+**，需要可执行的 `blastp` / `makeblastdb`（见 §5）
- **磁盘**：稳态运行 **≥ 6 GB**；全量重建建议预留 **~25 GB**

磁盘要分两个数量级看，别混用：

- **稳态运行约 5 GB** —— MySQL 数据文件 2.7 GB + undo/系统表空间 1.6 GB + `for_*` 产出
  0.4 GB + BLAST 0.6 GB + 前端 `dist` 0.1 GB。
- **全量重建峰值约 10–12 GB** —— 上述稳态 + 中间产物 `_src/` 0.6 GB + MySQL 临时文件数 GB
  + 每轮 binlog 约 1.3 GB。

**binlog 是累积项，不是稳态需求。** 我们开发机的 MySQL 数据目录现在占 19 GB，其中 12 GB 是
binlog —— 那是几十轮 ETL 攒下来的。全新安装没有这部分，`PURGE BINARY LOGS` 可以回收。
只按「目录有多大」估容量会严重高估。

库内部占用高度集中：`search_index` 一张表就占 2.0 GB（约库的 75%），其次 `gene_sequence_link`
0.32 GB，`evidence` 与 `enzyme` 各 0.12 GB，其余十一张表合计不足 0.2 GB。

## 4. MySQL 配置

有两项**必须显式设置**，不能依赖默认值：

```ini
datadir=<数据盘>/mysql-data       # Windows 例：D:/mysql-data；Linux 例：/data/mysql-data
tmpdir=<数据盘>/mysql-data/tmp    # 必须显式指定
innodb_buffer_pool_size=2G
innodb_buffer_pool_instances=1
```

- **硬性要求是「两者都不落在系统盘」，不是「落在某个具体盘」。**
- `tmpdir` 不显式配置时，MySQL 会把临时文件放进系统盘的用户临时目录。全量 ETL 会产生数 GB
  临时文件，系统盘写满后 ETL 会以 `OS errno 28 - No space left on device` 崩在中途。
- binlog 路径跟 `datadir` 走（`log-bin` 配的是相对路径），所以 `datadir` 换到数据盘后，
  binlog 的增长也落在数据盘 —— 这正是要的效果。
- Windows 上若 `datadir` 原本在系统盘，搬移需要管理员权限，且要同时改 `my.ini` 与目录 ACL。
  **建议装完 MySQL 立刻设置**，比事后搬移省事得多。

## 5. 依赖与外部程序

采集段与查询段**刻意使用两套不同的数据库驱动链**。三套依赖互不通用：

- **`backend/requirements.txt`** —— `fastapi`、`uvicorn[standard]`、`sqlalchemy[asyncio]`、
  `aiomysql`、`pydantic` v2、`pydantic-settings`、`python-dotenv`、`httpx`、`openpyxl`、`rdkit`
- **`etl/requirements.txt`** —— `pandas`、`sqlalchemy`、`pymysql`、`requests`
- **`update_tool/requirements.txt`** —— `requests`、`rdkit`

**两条驱动链不能互换。** 后端走 `create_async_engine`（故用 `aiomysql`），ETL 走
`create_engine`（故用 `pymysql`）。装了其中一个并不能跑另一个。

**ETL 不是纯离线步骤。** `etl_enzymes.py` 会调 UniProt REST 补本地文件里缺的字段；
`update_tool/` 里每个 fetch 步骤也都要连远程 API，所以两处都需要 `requests`。

**`rdkit` 出现在且仅出现在两处**，两处都只做「结构 → 键」这一件事：

- 离线，在 `update_tool/build_all_nodes.py`，从化合物 SMILES 现算 `compound.inchi_key_derived`。
- 在线，在后端 `app/utils/chemistry.py`，服务 `GET /api/v1/bundle/compound?smiles=`。

两处是**同一套转换，必须同 rdkit 版本**。**ETL 什么都不需要** —— 派生键在生成阶段就算完存进库。

**NCBI BLAST+ 的目录因机器而异，必须显式指定：**

- 可执行文件**不在本仓库里**。一份构建解压后数百 MB 且绑死平台，该带走的是下载地址。
- 默认值是相对后端启动目录的 `blast_bin/`；换位置用 `IGEM_BLAST_BIN_DIR` 指过去。
  文件缺失时后端明确报错，不会静默失败。
- 工作目录（FASTA 与 `makeblastdb` 产物）默认 `blast_work/`，用 `IGEM_BLAST_WORK_DIR` 覆盖。
- **把归档里的 `LICENSE` 一并留下。** 跑 BLAST+ 不需要它（那软件是美国政府作品、没有版权），
  但你只要以任何形式**再分发**它，这个文件就是该随件带上的声明 —— 归档里自带的那份才是权威版本。
- 路径含非 ASCII 字符时 BLAST+ 的索引库写不进去；后端会自动改用系统临时目录，功能不受影响，
  但索引缓存无法跨重启复用。

## 6. 配置与环境变量

后端所有配置经 `pydantic-settings` 使用 **`IGEM_`** 前缀，从 `backend/.env` 或环境变量读取：

- `IGEM_DB_HOST` —— MySQL 主机，默认 `localhost`
- `IGEM_DB_PORT` —— 默认 `3306`
- `IGEM_DB_USER` —— 默认 `root`
- `IGEM_DB_PASSWORD` —— 默认空
- `IGEM_DB_NAME` —— 默认 `igem_terpene`
- `IGEM_BLAST_BIN_DIR` —— BLAST+ 可执行文件目录，默认 `blast_bin`
- `IGEM_BLAST_WORK_DIR` —— BLAST+ 工作目录，默认 `blast_work`

ETL 读的是**同一套 `IGEM_DB_*` 变量名**，但有自己的配置（`etl/config.py`），且与后端不同 ——
**它只读环境变量，从不读 `.env`**。ETL 另有一个 `IGEM_DATA_DIR`，指向另一份 `for_*/`，
用于**沙箱验证**：在改动真实数据库之前，先在独立的库上验证「幂等」「编号不变」这类不变量。

> ⚠️ **绝不要把真实凭据提交进仓库。** `backend/.env` 里是明文数据库密码，
> 它必须写进 `.gitignore`，不得随仓库一起发布。

## 7. 数据来源

- **UniProt（主源）** —— `rest.uniprot.org`，检索词只有一个：`(terpene)`。单一检索词是刻意
  的设计：整个数据集因而是一个**快照**，分段只在本地按 `Reviewed` 列切。若拆成「已审阅 /
  未审阅各下一次」，两次下载就落在两个时间窗，跨窗口的条目升降级会让同一个条目同时落入两段、
  或被两段都漏掉。
- **Rhea** —— SPARQL（`sparql.rhea-db.org`）+ RDF。反应方程式、生理方向、EC 号、反应 SMILES。
- **ChEBI** —— EBI FTP 平面文件（4 个压缩包，共约 125 MB）。本地 SMILES / InChIKey 映射表，
  避免逐条查询；其中 `structures.tsv.gz` 92 MB，提供官方 `standard_inchi_key`。
- **UniProt 导出列** —— 离线解析、不打 API：GO 注释、异构体序列、参考文献（PubMed）、核酸编号。
- **DDBJ** —— `getentry.ddbj.nig.ac.jp`，核酸序列链接。
- **人工校订** —— 仓库内 `update_tool/chebi_data/curation_overrides.tsv`，对自动匹配结果的人工修正。

外部链接（NCBI、EBI、PubMed、DOI）仅用于生成跳转，不入库。

**实测规模（2026-09-20）：** 酶 95,869（1,535 Swiss-Prot + 94,334 TrEMBL）、反应 714、
化合物 734、酶–反应边 20,616、检索索引 3,756,596 行。两段之和与 UniProt 报告的总数一致，
一条不漏。

## 8. 灌库（TSV → MySQL）

```bash
cd etl && python -u etl_run.py                      # 全量灌库
cd etl && python -u etl_run.py --source=swiss_prot  # 只重载单个来源
```

`-u` 用于关闭 Python 输出缓冲；不加它日志会积在内存里，看起来像卡住了。**`etl/` 里没有
`--dry-run`** —— dry run 只存在于上游采集器 `update_tool/update_database.py`。

六个阶段按固定顺序执行，前后另有维护动作：

1. **前置** —— 在内存里快照已有边编号（`etl_edges.snapshot_edge_ids`），再按来源清空目标行、
   **子表优先**（`etl_run.purge_source`）
2. `etl_compounds` → `compound`
3. `etl_enzymes` → `enzyme`、`enzyme_id_map`
4. `etl_reactions` → `reaction`、`reaction_compound`
5. `etl_edges` → `enzyme_reaction_edge`
6. `etl_master` → `gene`、`gene_sequence_link`、`evidence`、`enzyme_go`、
   `enzyme_isoform`、`enzyme_solubility_score`
7. `etl_search_index` → `search_index`

第 6 阶段内部也有顺序，而且顺序有意义：酶总表 → 基因信息 → 序列链接 → 证据 → GO 注释 →
**异构体 → 溶解度分数**。异构体必须排在溶解度之前，因为溶解度装载要用异构体编号去异构体表核对。

**幂等与编号稳定：**

- 重跑在结构上是安全的：程序**按来源清空再重载**，而不是全表 truncate。
- **`enzyme_id` 永不回收。** 编号来自 `enzyme_id_map`，它记录每个 UniProt 登录号首次拿到的
  编号，且从不删除行 —— 条目消失时只打 `retired_at` 标记。回收编号再分配给别的条目，会让所有
  引用该编号的外部链接**静默指向错误的酶**。
- **`edge_id` 会被复用**，做法是在清理**之前**先快照已有的边编号。

**已经踩过并记录下来的坑：**

- 清理与装载不在同一个事务里。中途崩溃会留下可恢复的半程状态，重跑一遍即可。
- `--source=<s>` 模式下 `reaction` **只插入、不更新**，因为同一条 Rhea 反应在不同分段里的方向
  可能不同；该模式还会跳过 `retired_at` 维护与退役登录号解析 —— 这两件事都需要全量视图。
- upsert 辅助函数返回的是**输入行数**，不是实际写入行数。重跑时日志打出同样的行数、而实际
  "0 new rows"，是预期且正确的。
- `search_index` 的行是**选择性清理**的：只有带 `enzyme_id` 的行才随来源一起删；不带的是
  化合物/实体级行，与来源无关，走单独的 upsert。若把这类行也按来源清，它们**永远匹配不上
  删除条件，每刷新一次就重复累积一份**。

## 9. 表结构

`sql/schema.sql` 负责建库并建 **15 张表**，是建表语句的唯一真相源 —— 灌库程序在运行时从这个
文件里抽取各条 `CREATE TABLE`。**不要手工建表。**

- `enzyme` —— 酶总表：UniProt ID、序列、来源类型（swiss_prot / trembl）
- `compound` —— 化合物：SMILES / InChI / InChIKey（官方 + 派生）/ ChEBI ID
- `reaction` —— 反应：Rhea ID、方程式、方向、EC 号
- `reaction_compound` —— 底物 / 产物链接，带 `role`
- `enzyme_reaction_edge` —— 酶–反应边（图谱主体）
- `gene`、`gene_sequence_link` —— 基因与核酸序列链接
- `enzyme_go`、`enzyme_isoform`、`evidence` —— GO 注释、异构体、证据
- `enzyme_solubility_score` —— 溶解度模型分数（§13）
- `pathway_cache` —— 后端运行期缓存，ETL 从不碰它
- `search_index` —— 检索索引（实测 3,756,596 行）
- `enzyme_id_map`、`enzyme_alias_map` —— 编号稳定性表（§15）

**必须有的索引：** `search_index` 上的 `idx_search_index_value_prefix (field_value(64))`。
缺了它检索会慢约 **500 倍** —— 这就是建表必须走 `sql/schema.sql` 的原因。

并非所有表都由灌库程序写入：`enzyme_alias_map` 对 ETL 而言是**只读**的（整个仓库里没有一条
针对它的 `INSERT`，由外部填充），`pathway_cache` 由建表语句创建、但只在后端运行时使用。

## 10. 起 Web 服务

仓库里**没有一键启动脚本**。服务要在两个终端里分别起，且 MySQL 必须先起来。

后端（在 `backend/` 目录，需提供数据库密码）：

```bash
uvicorn app.main:app --port 8000
```

接口文档随后在 `http://localhost:8000/docs`。所有接口都在 `/api/v1` 前缀下，响应统一为
`{success, data, meta?, error?}` 信封格式。路由分组共 **11 个**：`metadata`、`graph`、
`search`、`structure_search`、`enzymes`、`compounds`、`reactions`、`bundle`、`blast`、
`download`、`assets` —— 逐条端点清单与错误约定见 §12。

前端（在 `frontend/` 目录）：

```bash
npm install
npm run dev        # 开发服务器，默认 http://localhost:5173
npm run build      # tsc -b && vite build → dist/
```

前端以相对路径请求 `/api/v1`：开发时 Vite 把 `/api` 代理到 `VITE_BACKEND`（默认
`http://127.0.0.1:8000`）；生产环境要给 `dist/` 配一条 `/api/` 代理规则和 SPA 回退
（`try_files ... /index.html`）。

> **重启 MySQL 之后必须重启后端。** 连接池里的连接会全部失效，不重启的话每个请求都返回 500。

**结构检索的边界：** 是 **InChIKey 精确匹配**，**不是子结构搜索** —— 能命中「同一个化合物」，
不能命中「结构相似的化合物」。引用这项功能时请注意。

- 结构式**不是预生成的**：`GET /api/v1/assets/compounds/{chebi_id}/structure.svg` 实时向 ChEBI
  取图，只带一层进程内缓存（§12.3）。网页端的键在浏览器里由 Ketcher（Indigo/WASM）算出。
- 匹配**跨两列**：`compound.inchi_key`（ChEBI 官方 `standard_inchi_key`）**或**
  `compound.inchi_key_derived`（RDKit 在生成阶段从该行自己的 SMILES 算出）。两列只在 **2 个**
  化合物上分歧：`CHEBI:231826`、`CHEBI:53643` —— ChEBI 自己那条记录自相矛盾，`smiles` 与
  `standard_inchi` 描述的是不同立体异构体。这两个从两个入口都还够得着。响应里**只**出现官方那一列。

## 11. 下载与检索

这两项是站点的主要功能，各自是「一个薄 router + 一个厚 service」：

- 下载 —— `routers/download.py`（110 行）+ `services/download_service.py`（791 行）
- 检索 —— `routers/search.py`（187 行）+ `services/search_service.py`（974 行）+ `utils/query_parser.py`（168 行）

前端各一个页面：`pages/DownloadsPage.tsx`、`pages/SearchResultsPage.tsx`。

### 11.1 下载

**队列住在前端，服务端无状态。** 用户在主图、检索结果页、酶详情页点「加入队列」，条目存在浏览器
`localStorage`（`App.tsx` 的 `queuedEntities`）里，后端完全不知道队列存在。点 Download 时前端
`POST` 一次，body 里带**整个队列**。

接口（都在 `/api/v1` 前缀下）：

- `GET  /download/fields` —— 列选择器的字段全集，由 `field_catalog()` 现场从 `FIELD_MAP` 生成，
  前端**不镜像**这张表，所以后端加字段不用动前端。
- `POST /download/preview` —— 真列名、真行数、被忽略的字段名。**不落盘**。
- `POST /download/files` —— 落盘，返回 `{fileUrl, status, stats, unknownFields}`。
- `GET  /downloads/{filename:path}` —— 把刚落盘的文件发给浏览器。

**两种导出形态**（请求里的 `download_type`）：

- `enzyme` —— 一张平表，**一酶一行**。所有 to-many 关系（反应 / 基因 / 化合物 / 文献）的值用
  `"; "` 拼进同一个单元格，顺序按主键定死 —— 所以同一批输入**重复导出得到逐字节相同的文件**。
  （旧实现只留每个关系的首行，且顺序随 MySQL 的返回而定，多反应的酶会丢数据。）
- `pathway` —— 一个 ZIP，按目录树组织：`enzymes/enzymes.csv` 加每条通路一个
  `pathways/<route>/`，里面是每步一张 `step_N.csv`、一张带 `Step` 列的 `all_enzymes.csv`
  和一张 `pathway_diagram.md`。**某一步没有指定酶时，回退到库里全部催化该步化合物对的酶**，
  并在图里标出这一步是回退来的。

**格式是白名单，不在表里的直接报错**（旧代码没有 `else` 分支，`XLSX` 这种大小写不对的写法会
一个文件都不写却仍报成功）：

- 酶页：`fasta`（列表里排第一，这个库主要就是发它）、`xlsx`、`csv`、`tsv`、`txt`、`json`。
- 通路页：只有 `zip`。

> **FASTA 不是表格。** 它每条记录一行表头（由所选的四个字段用 `|` 拼成）加一行序列，
> 所以列选择器只给那四个字段；`sequence` 无论勾没勾都会追加到每条记录 —— 只勾 `sequence`
> 也能得到合法文件。没有序列的酶被跳过，并计入 `stats.sequences`。

实现上几处不能改错的地方：

- **`FIELD_MAP` 是唯一真相源** —— 34 个字段，分 6 组（enzyme / reaction / gene / compound /
  literature / model score）。前端的列选择器靠 `/download/fields` 拿它，不自己抄一份。
- **Content-Type 写死，不落回 `mimetypes.guess_type`** —— Windows 上它会把 `.csv` 判成
  `application/vnd.ms-excel`，浏览器于是把文件交给 Excel 而不是下载。
- **路径穿越防护** —— `{filename:path}` 会把 `..%2f..%2f.env` 解码后原样送到 handler。
  `_resolve_within_downloads()` 先 `realpath` 再要求结果仍留在下载根目录下，否则 404。
- **落盘目录** `backend/app/downloads/`（已 gitignore），文件名带时间戳。
- Excel 单元格上限 32767 字符，写入前按 `MAX_CELL_CHARS = 32000` 截断。

### 11.2 检索

**四个检索接口**，都在 `search` router 下：

- `GET  /search/entries` —— 卡片式结果，默认 20 条一页；`view_mode=graph` 时额外返回
  `graphHighlights`，让首页地图能高亮命中的边。
- `GET  /search/table` —— 表格式结果，富行聚合；`total` 是 **LIMIT 之前的真实命中数**，
  所以页面能诚实地写「2000 of 55712」。一次最多 2000 行（`le=2000`）。
- `POST /search/table/by-ids` —— 按显式 enzyme_id 列表出**同样的富行**。BLAST 命中由此复用
  表格页的整套展示与筛选，不必另写一套。
- `POST /search/pathways` —— 通路检索：start → (…via…) → end 的有序化合物链，返回去重后的
  若干条通路，外加一个「所有返回通路的并集」图（节点 / 单边 / 复合边组），客户端一次画完、
  再逐条高亮。

其余带检索性质的入口分散在别的 router：结构检索（`structure_search`，InChIKey 精确匹配，
边界见 §10 末尾）、序列比对（`blast`，命中走 `/search/table/by-ids`）、整实体取数（`bundle`）、
首页地图（`graph`）。

**查询串怎么变成 SQL。** `utils/query_parser.py` 是个递归下降解析器，支持裸词、`field:value`、
`AND` / `OR` / `NOT` 和括号，输出 **OR-of-ANDs 范式**（每个 clause 内部 AND，clause 之间 OR）。
不指定 `input_type` 时按形状**自动判型**：`ENZ\d+` → 酶号、`CHEBI:\d+` → 化合物号、`\d+(\.\d+){0,3}`
→ EC 号、`[A-Z]\d[A-Z0-9]{3}\d+` → UniProt 号、七到八位数字 → PubMed 号。

**执行分两条路，按 `search_index` 有没有数据切换：**

- **索引路径** —— 查 `search_index`（宽表，约 376 万行，占库容约 75%）。三段 `UNION ALL`，
  分数按**匹配方式**给：精确 = `weight × 4`、前缀 = `× 2`、包含 = `× 1`，最后
  `GROUP BY enzyme_id` 取 `MAX(score)`。
- **回退路径**（`_search_single_legacy`）—— 索引表不存在或为空时，按 `FIELD_CONFIG` 的
  17 个字段直接 JOIN 各业务表。**只有「空结果且没有圈定谓词」才回退**：有筛选时的空结果是
  正常的用户可见状态（圈了一个没有命中的物种），把它当成「索引不覆盖」的信号会白跑一次更贵的扫描。

索引路径上有三处决定了它快不快：

- **精确段用 `field_value_hash` 等值，而不是 `LOWER(field_value) = LOWER(:v)`** —— 后者把列包在
  函数里，`field_value` 又是无索引的 TEXT，于是每次都全表扫。hash 列有自己的索引，且写入侧
  用的是同一套算法（等价性有实测支撑：两种写法的 distinct 值数都是 34,777，且「一个 hash 对应
  多个不同小写值」的组数为 0）。
- **前缀能走索引，包含不能** —— `idx_search_index_value_prefix (field_value(64))` 只为前缀服务。
  没有它，前缀检索是 6.6–7.1 s；有它，0.013–0.098 s。`%x%` 用不上任何 B-tree，仍是全表扫，
  这是已知代价，不是可修的缺陷。
- **筛选推进每一段扫描内部**，不套在聚合之外。两种写法**等价**（谓词只依赖 `enzyme_id`，
  分数只依赖该酶自己的行，可交换），但套在外面一行扫描都省不下；推进段内后 MySQL 改拿
  `enzyme` 表当驱动表，只读约 4.9 万行而不是 376 万行（热态 4.1 s → 0.57 s）。

> **`source_types`（搜索集）与 `display_*`（结果页工具栏的显示筛选）是两层**，都进 SQL，
> AND 叠加。理由是「先按分数截断、再在客户端筛」筛出来的是「前 N 名里恰好属于该来源的那几个」，
> 与真实答案无关。多条件路径同理，靠 `(A ∪ B) ∩ F = (A ∩ F) ∪ (B ∩ F)` 保持等价。

**分数与排序。** 单条件路径 `ORDER BY score DESC`；多条件路径合并时取各条件的**最高分**，
最终按 `(-score, enzyme_id)` 定序 —— 原来用的是 Python `set` 的迭代顺序，于是每点一次筛选，
「前 2000 行」都可能换一批。

**`total` 是真值，不是页内条数。** 单条件走窗口函数 `COUNT(*) OVER ()`（在 LIMIT 之前数）；
多条件因为每个条件已取满 `MULTI_CONDITION_CAP = 300000`，而全库总共才约 95,869 个酶，
所以 `len(merged)` 就是精确值。

**两处会让人找错原因的边界：**

- 通用搜索框里的 `smiles` 字段是**纯文本 LIKE**，不做任何化学规范化 —— 把库里的 SMILES 原样
  粘进去能中，换个等价写法就消失（实测目标酶排到第 149 位）。要按结构查，走结构检索那条入口。
- 化合物参与检索时会撞上两条排除规则：`EXCLUDED_COMMON_COMPOUND_IDS`（辅因子，全站都不算节点）
  与 `name <> compound_id`（名字等于编号的行，是没有真名的占位）。但**两条规则的位置不对称**：
  查询值本身是辅因子编号时，`_search_single` 在分路**之前**就短路返回空，两条路都拦得住；
  而「按名字搜、命中一个辅因子」只由回退路径的 JOIN 过滤（`TABLE_FILTER`）拦下 ——
  索引路径不过那道 SQL。两个集合目前也不同步：后端那份是 **14 个**，建索引的
  `etl_search_index.py` 里另有一份只列了 **3 个**（水 / 质子 / 焦磷酸）的旧副本。

## 12. 前后端 API 接口

**一个前缀，一个信封，一套命名。**

- 所有接口挂在 `/api/v1` 下（`main.py` 的 `API_PREFIX`），共 **11 个 router、25 个端点**。
- 响应一律是信封 `{success, data, meta?, error?}`（`schemas/common.py` 的 `ApiResponse`）。
  `meta` 只在少数端点出现，例如 `/bundle/batch` 用它报 `{total, found, missing}`。
- **线上字段名一律 camelCase**，由 `CamelModel` 的 `alias_generator=to_camel` 从 Python 的
  snake_case 自动派生 —— SQL 与 Python 内部仍是 snake_case，前端只认 camelCase。
- 交互式文档在 `GET /docs`（FastAPI 自动生成的 OpenAPI）；`GET /` 是一句服务横幅。

**错误约定：可预期的坏输入返回 HTTP 200 + `success: false`。**

这条最容易被无意破坏，因为 FastAPI 的**必填参数校验会吐非信封的 422**。所以凡是「用户可以传错」
的参数都写成可选、把校验放进 handler：

- `bundle.py` 的 `kind` 故意**不标** `Literal`，`?smiles=` 故意是 `Optional[str]` 而不是
  `Query(...)` —— 写成必填，缺失时就变成 422 而不是信封。
- `search/table` 的 EC 前缀校验也写在 handler 里，非法值回 `invalid_ec_prefix`，不用 Pydantic 校验器。
- **唯一的例外是下载**：`routers/download.py` 用 `HTTPException`（400 / 404），前端
  `api.ts` 里也专门多写了一段兼容 `payload.detail` 的分支。两套错误风格按端点记，别以为全站统一。

错误码词表（SCREAMING_SNAKE）：`BAD_REQUEST`、`NOT_FOUND`、`INVALID_SMILES`、
`INCHIKEY_UNAVAILABLE`（后两个是 `/bundle/compound?smiles=` 专有，见 §5）、`BLAST_FAILED`、
`BLAST_NOT_INSTALLED`，外加一个**小写的历史遗留** `invalid_ec_prefix`。

### 12.1 25 个端点

- **`metadata`（1）** —— `GET /metadata/filter-options`。筛选器候选值。`organisms` 的取值域
  **随 `module` 变**：图 / 首页收窄到「有反应注释的酶」，table / blast / download 是全量域。
  不收窄的话，下拉里会出现一个「选了图就空」的物种。
- **`graph`（4）** —— `GET /graph`（首页地图）、`POST /graph/map-scope`（地图上的圈选检索）、
  `POST /graph/by-enzymes`（给定酶集合取子图）、`GET /graph/edge-groups/{id}/edges`（展开复合边）。
- **`search`（4）** —— 见 §11.2。
- **`structure_search`（1）** —— `GET /ketcher/search?inchikey=`，InChIKey 精确匹配，边界见 §10 末尾。
- **`enzymes` / `compounds` / `reactions`（4）** —— 详情页取数：`GET /enzymes/{id}`、
  `GET /compounds/{id}/card`、`GET /compounds/suggest`（输入联想）、`GET /reactions/{id}`。
- **`bundle`（3）** —— 一次拿齐一个实体的全部分支数据。
  `GET /bundle/{kind}/{entity_id}`（`kind` ∈ enzyme | compound | reaction | pathway）、
  `POST /bundle/batch`（批量；某条查不到不让整个请求失败，用 `meta` 报 found / missing）、
  `GET /bundle/compound?smiles=`（服务端用 RDKit 转键后按化合物寻址，见 §5）。

  > `smiles` 单独一条路由，而不是给上面那条加个参数：SMILES 含 `/ \ # + @ [ ]`，
  > 其中 `/` 哪怕编码成 `%2F` 也会在路由匹配**之前**被解码、把路径切断，所以它不能走路径段。

- **`blast`（2）** —— `GET /blast/subjects`（可比的库规模）、`POST /blast/search`。
- **`download`（4）** —— 见 §11.1。
- **`assets`（2）** —— `GET /compounds/{chebi_id}/structure.svg`、
  `GET /reactions/{rhea_id}/atom-map.svg`。这两个是**实时代理**，不是读库，详见 §12.3。

### 12.2 前端只有一个对接层

`frontend/src/api.ts`（1103 行）是**唯一**发请求的地方：20 个网络函数 + 一个 `request<T>()`
包装（`fetch` → 判 `response.ok` → 判 `payload.success` → 返回 `payload.data`）。
页面组件不直接 `fetch`。

- **开发期** —— Vite 把 `/api` 代理到 `VITE_BACKEND`（默认 `http://127.0.0.1:8000`）。
- **生产期** —— 反向代理把 `/api/` 转给后端，`dist/` 走 SPA 回退（见 §10）。
- 请求**一律用相对路径**，所以同一份 `dist/` 放在任何域名下都能跑，不必重新构建。

> **类型是手抄的，没有代码生成。** `api.ts` 里的响应类型与 `types.ts` 都是照着后端 schema
> 手写的；OpenAPI 只用于 `/docs`，不参与前端构建。后端改了字段名而前端没跟上时，
> TypeScript **不会报错** —— 那个字段在运行时静默变成 `undefined`。这是这套接口唯一的系统性风险。

### 12.3 后端并非「只读 MySQL」

`assets` 那两个端点会把请求**转发到外部网站**（`routers/assets.py`）：

- `structure.svg` —— 先问 ChEBI 的 backend API 要 `default_structure.id`，再要那张图。
- `atom-map.svg` —— 按 `rhea_id` 去 `rhea-db.org/rhea/{id}/svg` 取。Rhea 有四套等价编号
  （master 与三个方向解析形式，实测 18,611 行无例外地相差 +1/+2/+3），代码按这个顺序逐个试，
  因为聚合物反应没有图、偶尔是 master 那个 id 404。

结果是**带进程内缓存的**（`dict` + 1 小时 TTL，重启即失效）：

- 上游正常 —— 返回图，顺手缓存。
- 上游说「这个 id 没有图」—— 返回 **404**（确定的「没有」）。
- 上游连不上 —— 返回 **502**；如果缓存里有**过期**的那一份，就宁可返回过期图也不返回 502。

> 所以「后端无状态、唯一外部依赖是 MySQL 与 BLAST+」那句话**不准确**：结构图这一路每次都要
> 连 `rhea-db.org` 与 `ebi.ac.uk`。断网时页面不会崩，但**没缓存过的化合物就没有图**。
> 另外 `assets.py` 里写死了一个带联系邮箱的 `User-Agent` —— Rhea 要求客户端自报身份，
> 而 httpx 的默认 UA 会被它前面的 Cloudflare 规则挡下。

`compound.structure_image_url` 这一列**不是**这条路径的来源：它是 ETL 阶段拼出来的一个 ChEBI
网站链接（`displayImage.do?...`），而前端只要 `chebiId` 以 `CHEBI:` 开头就**忽略它**、
改打上面那个代理端点。所以那一列目前是条死字段。

## 13. 溶解度分数

这个数字是界面上最容易被误读的一个，单独说。

- **它是模型参考分，不是可溶性标签，也不是校准过的概率。**
- 库里**没有任何可溶性标签**，所以任何阈值都无法在本库上拟合或验证。套在这些分数上的阈值是
  **外来的假设**，不是本工作的结论。
- 基准集的阈值不可迁移：三个基准集各自的最优阈值在库里圈出的比例差 **28 倍**（0.35 圈中
  27.7%，0.71 圈中 1.0%）。因此界面上一律称它为**模型参考分**，从不写「可溶」。

分数由开源工具 **DeepSolNet** 使用其原始权重计算得出；DeepSolNet 需要蛋白语言模型
**ESM C 300M**（两者的许可见 §16）。**产分那一次运行不在本仓库里** —— 仓库里有的是产出的
逐酶分数表，已转换成与其他子表一致的形态
（`for_enzyme_detail/child_tables/uniprotkb_solubility_score.{swiss_prot,trembl}.tsv`；
无后缀的那一份装载程序**不读**）。入库时有过滤：canonical 行一律保留，异构体行**仅当该异构体的
序列与其 canonical 序列不同**时才保留；分数解析失败或找不到对应酶的行会被跳过并计数。

**三态膜蛋白，`unannotated` 不等于 `non-membrane`**（来自 UniProt 的 `ft_transmem` +
`cc_subcellular_location`）：

- `membrane` —— `TRANSMEM` 非空，或 `SUBCELLULAR LOCATION` 含 "membrane"。18.06%
- `non-membrane` —— 有 SL 注释、无 `TRANSMEM`、SL 不含 "membrane"。1.35%
- `unannotated` —— **两者皆空，即 UniProt 没有说**。80.59%

这一列是**高精度低召回**：标出来的可信，但覆盖不到两成。剩下八成无法判定，**不能当可溶读**。

**分数分布：** 最小 0.0112，中位 0.2754，最大 0.9914，而 **0.5 落在第 92.5 百分位** ——
这是「这个原始数字不能当概率读」最清楚的一个论据。

> **关于文件格式。** 这些表文件**没有注释行** —— ETL 用 `csv.DictReader` 读它们，`#` 开头的行会
> 被当成数据。所以模型出处写进了**列名**（`DeepSolNet Score`）而不是文件头。

## 14. 完整复现

换一批全新数据重建时按此执行；只刷新单一来源则只需重跑第 4、6、7 步。
**以下命令均假定当前目录是仓库根目录**（第 7 步会自行切到 `etl/`）。第 3–5 步需要联网。

1. **配置 MySQL** —— `datadir` / `tmpdir` / 缓冲池（§4）。每台机器只做一次。
2. **建库建表** —— `mysql -u root -p < sql/schema.sql`。秒级。
3. **装依赖** —— §5 的三套 requirements。数分钟。
4. **下载原始数据** —— `python update_tool/download_uniprot.py`。几分钟。
5. **分段产出** —— `python update_tool/run_all.py --source=swiss_prot`（约 9 分钟），
   再 `--source=trembl`（约 2.5–3 小时）。两个来源**互不覆盖**，顺序无所谓，但都必须跑完
   才能进第 6 步（`--merge` 会检查）。
6. **汇合** —— `python update_tool/run_all.py --merge`。几分钟。3 张汇合表（底物-产物对 /
   化合物 / 图节点）**不能按来源切开**：一对底物→产物天然由两个来源的酶共享。
7. **部署到 `for_*/`** —— `python update_tool/update_database.py --source=swiss_prot --dry-run`，
   对 `trembl`、`--merge` 各再来一次；确认报告后去掉 `--dry-run` 真跑。**这是三条命令，不是一条。**
   覆盖前会自动备份到带时间戳的备份目录。
8. **灌库** —— `cd etl && python -u etl_run.py`。约 41 分钟。

**两点容易踩的：**

- **第 7、8 步读取连接信息的方式不同。** ETL 只读环境变量、不读 `.env`，所以第 8 步前必须在
  当前 shell 里 `export IGEM_DB_PASSWORD=...`（Windows cmd 用 `set`）；后端则可以从
  `backend/.env` 读取。
- **各步依赖不同：** 第 4–7 步跑的是 `update_tool/requirements.txt`，第 8 步跑的是
  `etl/requirements.txt`，启动 Web 服务才需要 `backend/requirements.txt`。

## 15. 不变量与已知限制

数据可信度的依据 —— 也是设计上花力气最多的地方：

- **酶编号永不回收。** `enzyme_id_map` 记录每个 UniProt 登录号首次获得的编号，条目消失只打
  `retired_at`，绝不删行。这张表刻意不加外键，也永不参与 `DELETE` / `TRUNCATE`。
- **条目改号时编号接续。** UniProt 合并条目会把旧登录号降为 secondary；`enzyme_alias_map`
  把编号接到同一生物学实体，避免同一个酶仅因为改号就被当成新酶重新发号。
- **按来源分段替换，而非全表重建。** 数据更新以「来源」为最小替换单位；分段文件缺失时硬报错。
- **动态列宽。** 部分列是「一酶多值」的列族（多个核酸编号、多个酶、多篇文献），列数随数据增长。
  程序按列头推导列宽而非写死上限 —— 写死上限的后果不是报错，而是超出的部分**不存在**。

已知限制：

1. **结构检索是精确匹配**，不支持子结构或相似性搜索。
2. **`update_tool/` 的脚本必须经 `run_all.py` 驱动。** 它们的默认输入路径指向从未随仓库分发的
   文件名；只有 `run_all.py` 会传真实路径，所以单独跑某个 fetch 脚本会失败。
3. **重建耗时以小时计。** TrEMBL 段的联网步骤约 2.5–3 小时，且依赖外部 API 的可用性；
   网络步骤设有断点缓存，中断后可续跑。
4. **数据随 UniProt 版本漂移。** 本页规模数字是 2026-09-20 的快照，重新下载后条目数会变化。
5. **重启 MySQL 后必须重启后端**，否则每个请求都返回 500。
6. **溶解度分数是模型参考分**，不是校准过的概率；本库中没有任何阈值经过标签验证。
7. **含 `*` 的 SMILES 在任何入口都查不到。** 这种字符串描述的是 R 基或通式，不是分子，
   所以它没有 InChIKey，任何化学库也变不出来。带 SMILES 的 730 行里恰好有 20 行是这种
   （这也是只有 710 行有官方键的原因），它们无法通过结构检索命中。

## 16. 许可与引用

我们自己的产出按「代码 / 数据」拆成两个许可：

- **源代码** —— `backend/`、`frontend/`、`etl/`、`update_tool/`、`tools/`、`sql/`：
  **Apache License 2.0**，全文见 [`LICENSE`](../../LICENSE)。
- **派生的表与文档** —— `for_*/`、`docs/`、`.docx` 设计文档、`.png` 图片、JSON 数据文件：
  **CC BY 4.0**，全文见 [`LICENSE-DATA`](../../LICENSE-DATA)。

用两个而不是一个，是因为这是两类不同的产出：代码用带明确专利授权的软件许可；数据用
**与上游数据源同一个**许可，这样从流水线这头到那头署名链条是连续的。

**在 CC BY 4.0 下，你可以共享和改编这些数据，包括商用**，条件是要给出适当的署名、链到许可
原文、并注明是否做过修改。上游各源要求的署名行集中记在 [`NOTICE`](../../NOTICE) ——
它同时也是我们自己 CC BY 4.0 数据的署名声明。

上游数据源：

- **UniProt** —— CC BY 4.0（UniProt Consortium）
- **Rhea** —— CC BY 4.0（SIB / EMBL-EBI）
- **ChEBI** —— CC BY 4.0（EMBL-EBI）
- **DDBJ**（经 INSDC）—— **对使用与再分发均无限制。** 何况本库并不存 DDBJ 的记录，
  只存登录号并据此生成外链，所以无论如何都没有序列数据在本仓库被再分发。
- **NCBI BLAST+** —— NCBI 以公有领域软件分发；**本仓库不再分发它**，由使用者自行下载（§5）。

**溶解度那条链的许可。** DeepSolNet 的上游仓库在 README 里声明 MIT，但**截至 2026-10-01
该仓库并不包含 `LICENSE` 文件**（GitHub 的 license API 返回 404）—— 缺的是形式上的授权文本，
而非精神上的许可。它依赖的 ESM C 300M 最初由 EvolutionaryScale 以 Cambrian Open License
发布（允许商用但带署名条件），**现已转至 Chan Zuckerberg Biohub，改为 MIT 且不再设门禁**。
网上仍有大量文档在讲旧的 Cambrian 条款，DeepSolNet 的 README 也仍指向旧的下载地址 ——
**我们在 2026-10-01 直接对着上游源核对了当前条款**；若你要复用这条流水线，请自己再核一遍，
不要相信搜索结果。

**随仓库分发的第三方软件。** 前端的结构式编辑器 Ketcher（`frontend/public/ketcher_standalone/`）
是一份预构建的 **v3.18.0** 发行包（EPAM Systems，**Apache License 2.0**）。我们原样分发，**一个字节
没改**。Apache License 2.0 §4 要求再分发者随附许可全文、上游 NOTICE 与原有的版权声明，所以我们把
EPAM 自己的 `LICENSE` 和 `NOTICE` 都放在它旁边（两份都与上游 `v3.18.0` 标签逐字节相同），并保留了
构建自带的 `*.LICENSE.txt` sidecar —— 那几份 sidecar 不能删，每个打包后的 JS 文件末尾都有一句
指向它的横幅。详见 `docs/wiki/database-protocol.md` §12.4 与 [`NOTICE`](../../NOTICE)。

**引用：** UniProt（https://www.uniprot.org）、Rhea（https://www.rhea-db.org）、
ChEBI（https://www.ebi.ac.uk/chebi）、DDBJ（https://www.ddbj.nig.ac.jp）、
NCBI BLAST+（https://www.ncbi.nlm.nih.gov）、ESM / ESMC（https://github.com/Biohub/esm）、
DeepSolNet（https://github.com/wangxinglong1990/DeepSolNet）、
Ketcher（随前端分发，https://github.com/epam/ketcher）。

> **本节不构成法律意见。** 我们只是把上游条款在我们理解范围内的内容、按上述日期记录下来。
> 在依赖其中任何一条之前，请自行阅读许可原文。
