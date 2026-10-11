**中文** | [English](README.en.md)

# EDGE — IGEM 代谢通路数据库

NJU-CHINA 参加 IGEM 比赛的专用数据库。以萜类合酶及相关化合物为核心，
构建"酶-反应-化合物"图结构，支持可交互通路浏览、条目/通路检索、同源检索和数据下载。

数据规模（2026-10-02 实测）：**95,869 个酶**（1,535 Swiss-Prot + 94,334 TrEMBL）、
714 个反应、734 个化合物、20,616 条酶-反应边、3,756,596 行检索索引。

---

## 环境要求

前端使用浏览器 `localStorage` 自动保存搜索关键词、搜索集、表格筛选条件、最近完成并进入结果页的 BLAST 结果以及下载队列。刷新或重新打开同一站点时自动恢复；清空下载队列也会同步更新缓存。缓存按站点地址隔离，清除站点数据可重置；损坏的缓存会回退为默认值，存储不可用或空间不足时仍可在当前页面正常操作。接口数据仍会重新获取，图上的临时展开、拖拽布局和未提交的通路编辑不在本次缓存范围内。

页面由 URL 路由决定，支持复制链接、刷新直达以及浏览器前进/后退：`/` 为首页图谱，`/search?q=terpene` 为关键词检索，`/downloads` 为下载表，`/enzymes/<编码后的酶ID>` 为酶详情。打开 `/` 始终进入首页，不再被旧的页面缓存覆盖。BLAST 表格使用 `/search?mode=blast`，其结果仍属于当前浏览器的缓存，不能通过链接分享给其他浏览器。未知路径会回到首页。

`npm run dev` 和 `npm run preview` 支持这些路径。部署 `dist` 时需为前端启用 SPA 回退（例如 Nginx 的 `location / { try_files $uri $uri/ /index.html; }`），并保留独立的 `/api/` 代理配置，否则直接访问详情 URL 可能返回服务器 404。

- **Python 3.10+**（含 pip）
- **MySQL 8.0+**
- **Node.js 20+**（前端用；以 `frontend/package.json` 的 `engines` 为准）
- 磁盘：**稳态运行约 5 GB**；全量重建建议预留 **25 GB 以上**（实测明细见 [`docs/wiki/database-protocol.md`](docs/wiki/database-protocol.md) §3.1）

---

## 全量部署（原始数据 → 可检索数据库）

> 这是**从零建库**或**换一批全新数据重建**的完整流程，共 7 步。
> 只想刷新某一个来源、不重建全库的话走 §日常更新，比这里短得多。

### 0. MySQL 准备（每台机器只做一次）

```ini
# my.ini  —— 三项都必须显式设置，别用默认值
datadir=D:/MySQLData/
tmpdir=D:/MySQLData/tmp
innodb_buffer_pool_size=2G
innodb_buffer_pool_instances=1
```

`tmpdir` **必须显式配**。MySQL 默认把临时文件放在 `C:\Windows\ServiceProfiles\...\Temp`，
而全量 ETL 会产生多 GB 临时文件；C 盘一旦满，ETL 会在第 6 步以
`OS errno 28 - No space left on device` 崩掉，且只跑到一半。

> **这三件事本仓库不提供脚本。** 我们当初是在本机写的 PowerShell 脚本，但它们绑死了
> 那台机器的盘符与 MySQL 安装路径（`D:\MYSQL\bin\mysql.exe`、服务名 `MYSQL80`），
> 而且其中删旧 datadir 的一步不可逆 —— 不适合随仓库分发。要做的操作本身如下，
> 在**管理员权限**的 PowerShell 里逐条执行即可。

**① 把 datadir 搬离 C 盘**

```powershell
Stop-Service MYSQL80 -Force
robocopy 'C:\ProgramData\MySQL\MySQL Server 8.0\Data' 'D:\MySQLData' /MIR /COPYALL /XJ /R:1 /W:1
# 比对两边的「文件数 + 总字节」；不一致就到此为止，Start-Service 退回原状
# 一致 → 备份 my.ini，把 datadir= 改成 datadir=D:/MySQLData
icacls 'D:\MySQLData' /grant 'NT AUTHORITY\NetworkService:(OI)(CI)F' /T
Start-Service MYSQL80
```

服务起来、应用能正常读之后，**再人工删除 C 盘那份旧目录**（这一步不可逆，别提前做）。

> `my.ini` 是 **UTF-8 无 BOM**，且注释里有一个 U+2212 减号。编辑时要用
> `New-Object System.Text.UTF8Encoding($false)` 读写；按 ASCII 重写会把那个字符悄悄换成 `?`。

**② 给 `[mysqld]` 补 `tmpdir`** —— 先建好目录，再加一行，然后重启服务：

```ini
tmpdir=D:/MySQLData/tmp
```

**③ 缓冲池落盘** —— 同样加进 `[mysqld]` 再重启服务：

```ini
innodb_buffer_pool_size=2G
innodb_buffer_pool_instances=1
```

改完确认一遍：

```sql
SELECT @@datadir, @@tmpdir, @@innodb_buffer_pool_size, @@innodb_buffer_pool_instances;
-- D:\MySQLData\  D:/MySQLData/tmp  2147483648  1
```

### 1. 建库建表

```bash
mysql -u root -p < sql/schema.sql
```

`schema.sql` 会建 15 张表，其中包含检索索引需要的
`idx_search_index_value_prefix (field_value(64))` —— 少了它检索会慢 500 倍，别手工建表。

### 2. 装依赖

```bash
pip install -r etl/requirements.txt          # 建表段
pip install -r update_tool/requirements.txt  # 采集段（第 3–5 步要用）
pip install -r backend/requirements.txt      # 后端
cd frontend && npm install && cd ..
```

### 3. 下载原始数据

```bash
python update_tool/download_uniprot.py
```

一次下载整个检索集 `(terpene)`，再**在本地按 `Reviewed` 列**拆成两个分段文件：

```
update_tool/_src/swiss_prot/output_uniprot_unified.tsv   1,535 行
update_tool/_src/trembl/output_uniprot_unified.tsv      94,334 行
```

两段合起来 = UniProt 报的 `total`，一条不漏。这一步约几分钟、95,869 行、21 列。

> **检索词只有一个。** 不要再退回「一来源一个词各下一次」的旧做法 —— 那是两个时间窗，
> 跨窗口的升/降级会让同一个 accession 同时落进两段、或被两段都漏掉。

### 4. 分段产出

```bash
python update_tool/run_all.py --source=swiss_prot    # 约 9 分钟
python update_tool/run_all.py --source=trembl        # 约 2.5–3 小时（联网步骤多）
```

每个来源各产出 11 张表到 `update_tool/_src/<source>/`。两个来源**互不覆盖**。

顺序无所谓，但两个都必须跑完才能进下一步 —— `--merge` 会检查。

### 5. 汇合

```bash
python update_tool/run_all.py --merge
```

跨条目聚合的 3 张表（底物-产物对 / 化合物 / 图节点）从**全部**分段重建，落在 `update_tool/_merged/`。
这三张不能按来源切开：一对底物→产物天然由两个来源的酶共享。

### 6. 部署到 `for_*/`

```bash
cd update_tool
python update_database.py --source=swiss_prot --dry-run
python update_database.py --source=trembl     --dry-run
python update_database.py --merge             --dry-run
# ↑ 看三份报告确认无误，然后各去掉 --dry-run 真跑一遍
```

**是三条命令，不是一条。** 覆盖前会自动备份到 `_backup_<时间戳>/`。

落盘位置（注意第 11 张不在 `for_*/` 下）：

```
for_enzyme_detail/child_tables/uniprotkb_{go,isoform_sequences,names_split,references,rhea,sequence_links}.<source>.tsv
for_enzyme_reaction_card/uniprotkb_{enzyme_merged,rhea_summary}.<source>.tsv
for_enzyme_detail/uniprotkb_master.<source>.tsv
for_graph/uniprotkb_terpene_only.<source>.tsv
uniprotkb_terpene_parsed.<source>.tsv                    ← 落在**仓库根目录**
for_graph/uniprotkb_terpene_pairs.tsv                    ← 以下 3 张无后缀（汇合）
for_compound_card/uniprotkb_terpene_compounds.tsv
for_graph/all_nodes.tsv
```

### 7. ETL 灌库

```bash
cd etl
export IGEM_DB_PASSWORD='你的密码'    # Windows cmd: set IGEM_DB_PASSWORD=你的密码
python -u etl_run.py
```

约 **41 分钟**，6 步：enzymes → compounds → reactions → edges → master → search index。
建索引那一步占约 20 分钟。

加 `-u` 关掉 Python 的输出缓冲 —— 否则日志会憋在内存里，看起来像卡住了。

灌完后核对：

```sql
SELECT source_type, COUNT(*) FROM enzyme GROUP BY source_type;   -- 1535 / 94334
SELECT COUNT(*) FROM enzyme_id_map WHERE retired_at IS NOT NULL; -- 0
```

### 8. 起服务

```bash
# 后端（另开一个终端）
cd backend
export IGEM_DB_PASSWORD='你的密码'
uvicorn app.main:app --port 8000

# 前端
cd frontend
npm run dev
```

访问 **http://localhost:5173**；API 文档 **http://localhost:8000/docs**。

### 各步耗时与产出（本机实测）

| 步 | 命令 | 耗时 | 产出 |
|---|---|---|---|
| 3 | `download_uniprot.py` | 几分钟 | 2 个分段原始表（1,535 / 94,334 行） |
| 4 | `run_all.py --source=swiss_prot` | 约 9 分钟 | `_src/swiss_prot/` 11 张 |
| 4 | `run_all.py --source=trembl` | 约 2.5–3 小时 | `_src/trembl/` 11 张 |
| 5 | `run_all.py --merge` | 几分钟 | `_merged/` 3 张 |
| 6 | `update_database.py` ×3 | 秒级 | `for_*/` 14 张落盘 |
| 7 | `etl_run.py` | 约 41 分钟 | MySQL 14 张表（库共 15 张，见 §MySQL 数据表） |

---

## 注意事项

### A. 磁盘与 MySQL（最容易让全量跑挂的一条）

- **MySQL 的 datadir / tmpdir 都不许在 C 盘。** 见 §0。全量 ETL 每次写约 **4 GB binlog**
  和数 GB 临时文件，落 C 盘必然撑爆。
- **binlog 跟 datadir 走**（`log-bin` 是相对路径），所以 datadir 搬到 D 盘后 binlog 增长也在 D 盘。
- **重启 `MYSQL80` 之后必须重启 uvicorn。** 后端连接池里的 aiomysql 连接会全部失效，
  每个请求回 500，直到 uvicorn 重启。
- 若端口被占（`[Errno 10048]`）而后端却"一直回 500"，通常是**上一次的 uvicorn 进程还活着**：
  `netstat -ano | grep :8000` 找到 PID，`taskkill //PID <pid> //F`。

### B. 命令必须显式带 `--source=` 或 `--merge`

`run_all.py` 和 `update_database.py` 都**强制**二选一，裸跑会打印用法并退出。
这是分段设计的入口闸，不要为了"方便"给它加默认值。

### C. `enzyme_id` 的规则 —— 改动数据前务必读这一节

- `enzyme_id_map` 是**编号永久不变**的唯一保证：一个 accession 已经在表里，
  重跑后一定拿回同一个号（实测 95,869/95,869，0 个新分配）。
- 因此 **`enzyme_id_map` 永不参与任何 `DELETE` / `TRUNCATE`**，
  它刻意**不加** `FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)` ——
  条目从新版本消失时 `enzyme` 行会被替换删掉，而映射行必须活下来。
- **还原数据库时要连它一起还原。** 只还原 `enzyme` 而不还原 `enzyme_id_map`，
  下次 ETL 会重新分配编号，已分发出去的 ENZ 号就指向别的酶了。
- 编号**只增不减、绝不填洞**：条目消失后它的号永久保留，新条目取 `MAX+1`。
- **编号不只出现在 `enzyme.enzyme_id` 一处**，查问题时别只看那一列。
  `search_index.entity_id` 里也有：`entity_type='enzyme'` 的行就是 `ENZ……`，
  `entity_type='evidence'` 中缺 DOI/PMID 的兜底行是 `ENZ……:reference:N`。
  实测：从零建库与真库比，`search_index` 会有 46,410 组"差异"，
  **归零只需要把这两处的酶号也换回稳定身份** —— 即差异全在编号、内容逐行相同。
- **编号接续**（条目改主 accession 时把旧号接过去）走的是另一条路 ——
  `enzyme_alias_map` + `resolve_vanished` 的 302 重定向解析，有 `REDIRECT_LIMIT = 500`，
  且 `--source=<s>` 模式下**整步跳过**。别把它和上面的"编号保留"当成一件事。

### D. 单来源刷新 vs 全量：两处只有全量才能做的判断

单来源模式只看得到半张表，所以有两件事它**无权做**，代码里都做了显式跳过（不是遗漏）：

| 事情 | 单来源模式的行为 | 为什么 |
|---|---|---|
| `enzyme_id_map.retired_at` 维护 | **整步跳过**并打印说明 | 判"条目消失"需要全量视图 |
| `reaction` 的属性（`direction` 等） | **纯只新增**（`INSERT IGNORE`） | 同一个 Rhea ID 在两个来源里的 `Direction` 可以不同；只有一个 Rhea ID 一条反应这个跨来源的客观实体，只有合并视图有权决定它取哪一行 |

实测过不这么做的后果：只跑 `--source=trembl` 会把 1,535 个 SwissProt 条目的
`retired_at` 全部打上（假的"已消失"标记），并把 12 行 `reaction.direction` 从
`forward`/`reverse` 覆盖成 `unknown`（图上多出镜像边）。两处都已修。

### E. 幂等性与已知偏差

输入一字不改重跑 ETL，14 张表逐行不变（沙箱 A/A'/B/C/D 五项验收全过）。
**唯一的已知偏差**：`search_index` 每次重跑 **+16 行** —— 源侧存在 24 组完全重复的
输入行，被切片边界分开。行内容正确，只多占几十行，不影响任何查询结果。**这是接受的。**

### F. 断点缓存与重跑

| 步骤 | 缓存 | 失败后 |
|---|---|---|
| `fetch_isoform` / `fetch_references` / `fetch_sequence_links` | `_*_cache.json`（成功即删） | 删掉半截输出重跑即可续传 |
| `fetch_rhea` / `build_terpene_only` | **无缓存** | 该步整体重跑 |
| `build_all_nodes` | **无缓存**（纯离线，几秒跑完） | 该步整体重跑 |

`run_all.py` **默认跳过已存在的输出**；要强制全重跑加 `--force`。

### G. 已删除的旧入口

这两个脚本已于 2026-10-02 从 `update_tool/` 删除，在此留档说明它们为什么不在了：

- **`run_workflow.py` / `run_workflow.sh`** —— 分段改造**之前**的旧入口，已经坏了：
  它第 ② 步调 `run_all.py --force --out-dir=... <表>` 而不带 `--source`，会直接打印用法退出；
  它的检索词还写死 `reviewed:true`。**没有去补它**，因为补了等于暗示它还能用。
- **`update_tool/fetch_inchikey.py`** —— 不接任何流程。它产出 6 列并**原地覆盖**
  `for_graph/all_nodes.tsv`（现表是 4 列），且绕过 `update_database.py` 的备份。
  功能已由 `build_all_nodes.py` 承担（它现在读 `chebi_data/structures.tsv.gz`，不联网）。

### H. 修改工具代码时

- **`--source=<s>` / `--merge` 的边界是「一行是否对应一个 UniProt 条目」**，
  不是"看起来能不能切开"。跨条目聚合的表硬切会产生重复行和重复节点。
- **`update_database.py` 是 `shutil.copy2` 整文件覆盖**，`for_*/*.tsv` 每张表只有一个物理位置。
  这是为什么隔离必须在**产出阶段**做（`_src/<source>/`），不能靠部署阶段。
- **来源登记只有一份**：`update_tool/source_registry.py` 的 `SOURCES`。
  加新来源时**只改这里**；`--merge` 的安全阀按它逐个校验，写第二份会漂移。
- 产出表里 **`Source` 列一律放末列** —— `references` / `sequence_links` / `master`
  的列宽是**动态**的，只有尾部位置才能保证列名→列号映射稳定。
- 全量 ETL 后 `search_index` 有几百万行，**改索引相关的写法前先看 §验证 的探针**。

---

## 日常更新

### 只刷新一个来源

```bash
python update_tool/download_uniprot.py            # 1 次下载, 本地拆分
python update_tool/run_all.py --source=trembl --force
python update_tool/run_all.py --merge             # 汇合必须重跑
python update_tool/update_database.py --source=trembl
python update_tool/update_database.py --merge
cd etl && python -u etl_run.py --source=trembl
```

`--source=trembl` 只替换 TrEMBL 的行，SwissProt 的行与编号**一个都不动** ——
这就是分段隔离的意义（已实测：只有 `search_index` +16 行那个已知偏差出现）。

⚠️ **`--merge` 必须跟着重跑**，因为汇合表读的是全部来源的 `terpene_only`。

⚠️ **`etl_run.py --source=<s>` 是半张视图**，见 §D —— 编号退休标记与共享反应属性那两件事它会跳过。

### 换新数据 / 回到某个历史状态

- **换新数据**：从 §3 重跑一遍即可（分段会整体重新产出）。
- **回到某个历史状态**：用 `_db_backup/` 里的 `mysqldump` 还原，**不要**指望重跑 ETL 复现。

**这一条在 2026-09-20 实测过**（从零建库 `igem_terpene_repro` 灌同一份 `for_*/`，再逐行比）：

| 层 | 结论 |
|---|---|
| 产出 → 落盘 → 库的内容 | **完全可复现**：14 张表行数逐张相同，内容**逐行**相同（按稳定身份 `uniprot_id` 对齐、多重集相等判定） |
| **编号** | **不可复现**：1,535 个 SwissProt 酶的 `enzyme_id` 与真库不同（TrEMBL 那 94,334 个恰好相同） |

原因：`enzyme_id` 是**状态的载体、不是数据的函数** —— 真库那 1,535 个编号来自更早的
位置编号方案，扩容时靠 `enzyme_id_map` 沿用；从零建库没有这段历史，于是重新分配
（新号总是 `MAX+1`，所以从 `ENZ000001` 起走，与真库错开）。

**要"补回"编号**：把真库的 `enzyme_id_map` 与 `enzyme.enzyme_id` 搬过去再重跑 ETL 即可 ——
因为编号的唯一来源就是那张表（实测 95,869/95,869 全部沿用、0 个新分配）。

---

## 验证

我们开发时用一组探针脚本守着管线，改动后按需跑。它们**不在本仓库里**（也**已从工作区删除**，
见 [`docs/wiki/database-protocol.md`](docs/wiki/database-protocol.md) §2.3）—— 每个都要连上本机的
`igem_terpene` 库和 `for_*/` 才能跑，路径写死在开发机上。下表说明每个探针验的是什么，
方便你自己写一个：

> 命名约定：打印耗时、累积 `failures` 列表、非零退出、末尾打印 `ALL CHECKS PASSED`。
> Windows 控制台是 GBK，跑之前设 `PYTHONIOENCODING=utf-8`。

> **别把它们想成「只读」**：绝大多数确实只查不写，但有两个改过库 —— 一个对 `search_index`
> 跑 `ALTER TABLE ... ADD INDEX`（就是 §1 里那个前缀索引），另一个清空过
> `performance_schema` 的监控表。自己写探针时，写库语句要单独拎出来、别混进顺手跑的脚本里。

| 检查项 | 验什么 |
|---|---|
| 全链条贯通 | 产出 ↔ `for_*/` 逐字节、**ETL 自己的读取层**解析到几个文件读回多少行、文件 ↔ 库逐表对齐 |
| 编号保留 | 活映射 → 全部沿用；空映射 → 全部重分配；退休号不回收 |
| 重灌比对 | 把同一份 `for_*/` 重灌进另一个库，与真库**逐行**比（按稳定身份 `uniprot_id` 对齐、多重集相等） |
| 搜索集 | 检索 / 搜索集的划分性、total 真实性、EC 与物种下推等价 |
| BLAST 计数 | BLAST 计数接口 == 真跑一次的 `searchedSubjects` |
| 首页图 payload | 与基线逐项一致 |

部署后的人工冒烟：

1. `GET /metadata/filter-options` 的 `sourceTypes` 含 `trembl`
2. `GET /search/entries?q=synthase&source_types=trembl` 满页且**含没有反应边的酶**（取数域没被换成有边子集）
3. `GET /enzymes/{id}` 打开一个 TrEMBL 酶，来源徽标显示 `trembl`
4. `GET /graph?selection_mode=global&limit_nodes=120` 返回 200，边上的酶**全部**有反应边
5. `GET /blast/subjects` 的计数随搜索集在 95,899 / 1,565 / 94,334 之间变

---

## 回滚

| 层 | 怎么退 |
|---|---|
| 数据库 | `_db_backup/igem_terpene_*.sql` 还原（`mysql -u root -p < 备份.sql`） |
| `for_*/` 旧表 | `python update_tool/update_database.py --restore=<备份目录>`（覆盖前自动建的那个） |
| 产出文件 | 重跑 `run_all.py`（输入没变的话结果逐字节相同） |

---

## 项目结构

```
igem_database/
├── sql/schema.sql                  # 建表脚本（15 张表 + 索引；ETL 灌其中的 14 张）
├── update_tool/                    # 数据获取与重建工作流
│   ├── download_uniprot.py         # ① 下载 + 本地按 Reviewed 拆段
│   ├── run_all.py                  # ② 分段产出 ③ 汇合
│   ├── update_database.py          # ④ 部署到 for_*/
│   ├── source_registry.py          #   来源的唯一登记处（加来源只改这里）
│   ├── chebi_data/                 #   自带 ChEBI 参考库
│   ├── _src/<source>/              #   各来源的 11 张分段产出
│   ├── _merged/                    #   3 张汇合产出
│   └── WORKFLOW.md                 #   工作流维护手册（部分内容早于分段改造）
├── etl/                            # ⑤ TSV → MySQL
│   ├── config.py                   #   连接配置（环境变量可覆盖）
│   ├── etl_run.py                  #   一键执行 6 步
│   └── etl_*.py                    #   各表导入
├── backend/                        # FastAPI 后端
│   ├── app/{main,config,database}.py
│   ├── app/{models,schemas,routers,services,utils}/
│   ├── .env.example                #   环境变量模板（复制成 .env 后填值）
│   └── .env                        #   数据库密码（不提交 git）
├── frontend/                       # React + Vite 前端
├── for_enzyme_detail/              # 落盘的 TSV（分段带 .<source> 后缀）
├── for_enzyme_reaction_card/
├── for_compound_card/
└── for_graph/
```

下面这些**不在仓库里**，是本机跑起来才会出现的（全都在 `.gitignore` 里）：
`backend/.env`（数据库密码）、`backend/blast_bin/` 与 `backend/blast_work/`（§3.4）、
`update_tool/_src/` 与 `_merged/`（中间产物）、`_db_backup/`（mysqldump 备份）、
以及开发时那组验证探针（**已删**，见上）。

`update_tool/` 的隔离原则：**只读**原始目录与 `chebi_data/`，全部输出写到自己的目录。
**绝对不要修改原始文件夹里的任何文件。**

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IGEM_DB_HOST` | `localhost` | MySQL 主机 |
| `IGEM_DB_PORT` | `3306` | MySQL 端口 |
| `IGEM_DB_USER` | `root` | MySQL 用户名 |
| `IGEM_DB_PASSWORD` | *(空)* | MySQL 密码，**ETL 与后端必填** |
| `IGEM_DB_NAME` | `igem_terpene` | 数据库名（可指向另一个库做复现/沙箱） |
| `IGEM_DATA_DIR` | 仓库根 | ETL 读哪份 `for_*`（沙箱用） |
| `IGEM_BLAST_BIN_DIR` | `blast_bin` | BLAST+ 可执行文件所在目录（空串 = 用系统 PATH） |
| `IGEM_BLAST_WORK_DIR` | `blast_work` | BLAST+ 工作目录，路径必须全 ASCII |
| `PYTHONIOENCODING` | — | Windows 控制台跑中文输出的脚本时要设 `utf-8` |

后端也可通过 `backend/.env` 配置，模板见 `backend/.env.example`。**注意 `backend/.env` 只喂后端**——ETL 是在进程环境里直接读这些变量，不加载该文件。密码通过环境变量传递，**不要写进命令行**（会出现在进程列表里）。

---

## MySQL 数据表（2026-10-02 实测行数）

| 表名 | 行数 | 说明 |
|---|---|---|
| `enzyme` | 95,869 | 酶静态属性（1,535 Swiss-Prot + 94,334 TrEMBL） |
| `enzyme_id_map` | 95,869 | **编号永久映射，永不删除**（见 §注意事项 C） |
| `enzyme_alias_map` | 0 | 编号接续（条目改号时） |
| `enzyme_reaction_edge` | 20,616 | 图中边（酶 ↔ 反应） |
| `reaction` | 714 | 反应事实（Rhea），跨来源共享实体 |
| `compound` | 734 | 化合物节点（ChEBI，过滤通用小分子） |
| `reaction_compound` | 1,557 | 底物/产物关系 |
| `gene` | 92,252 | 酶的基因链接 |
| `gene_sequence_link` | 620,808 | INSDC/RefSeq 外部序列链接 |
| `evidence` | 112,720 | 文献证据 |
| `enzyme_go` | 75,874 | GO 注释（仅生物过程 BP） |
| `enzyme_isoform` | 56 | 同工型序列 |
| `enzyme_solubility_score` | 95,899 | 每酶的模型参考分 + 膜注释。**外部管线产出**（见 `NOTICE` › Not redistributed），不由 `update_tool/` 生成 |
| `search_index` | 3,756,596 | 聚合全部字段的检索索引 |
| `pathway_cache` | 0 | 通路结果缓存（**ETL 绝不触碰**） |

库中共 **15 张表**。上表中 `pathway_cache` 是唯一 ETL 从不写入的一张（`etl/` 全目录零引用），其余 **14 张由 ETL 灌入**——`enzyme_solubility_score` 也在其中，尽管它的数据来自外部管线。

所以本节是 **15 行**。文中另有几处写"14 张表"（如 §注意事项 E 的幂等性结论、§换新数据 的复现性实测），指的都是 ETL 写入的这 14 张——口径不同，不是矛盾。

---

## API 接口

基础路径 `/api/v1`。完整文档：**http://localhost:8000/docs**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/metadata/filter-options` | 筛选项（`?module=graph\|table` 决定物种取值域） |
| GET | `/graph` | 网络图数据（`selection_mode=global` 或按酶） |
| POST | `/graph/map-scope` | 检索词 → 图范围 |
| POST | `/graph/by-enzymes` | 指定酶集合 → 图 |
| GET | `/graph/edge-groups/{id}/edges` | 展开重叠边 |
| GET | `/search/entries` | 条目检索（卡片，分页） |
| GET | `/search/table` | 表格检索（真实 `total`、筛选下推） |
| POST | `/search/table/by-ids` | 按显式 id 取行（BLAST 用） |
| POST | `/search/pathways` | 通路检索 |
| GET | `/ketcher/search` | 结构检索（按 InChIKey 找反应） |
| GET | `/enzymes/{enzymeId}` | 酶详情（含酶自身来源） |
| GET | `/compounds/suggest` | 化合物联想 |
| GET | `/compounds/{compoundId}/card` | 化合物卡片 |
| GET | `/reactions/{reactionId}` | 反应详情 |
| GET | `/bundle/{kind}/{entityId}` | 一次取回实体字段 + 其化合物结构图 URL（`kind` = `enzyme\|compound\|reaction\|pathway`） |
| GET | `/bundle/compound?smiles=` | 按 SMILES 取化合物（服务端用 RDKit 转 InChIKey；响应与 `/bundle/compound/{id}` 同构） |
| POST | `/bundle/batch` | 批量版（≤50 条，按入参顺序返回，单条查不到不影响整体） |
| GET | `/blast/subjects` | 当前搜索集下的 BLAST 库序列数 |
| POST | `/blast/search` | 发起 BLAST（按搜索集建库） |
| GET | `/download/fields` | 可导出字段 |
| POST | `/download/preview` | 下载预览 |
| POST | `/download/files` | 生成下载文件 |
| GET | `/downloads/{filename}` | 取回生成的文件 |
| GET | `/assets/reactions/{rheaId}/atom-map.svg` | 反应原子图 SVG |
| GET | `/assets/compounds/{chebiId}/structure.svg` | 化合物结构图 SVG |

`kind=pathway` 的 `entityId` 是一条化合物链（`PATH_A_B_C`），不是表里的一行 —— 它由
`POST /search/pathways` 返回，这里按同一套规则**现算**回来，`entity.graph` 只含这一条链的边。
id 里**不含筛选条件**，所以当初若用了 `sourceTypes`/`reviewStatuses` 搜索，取回时要带同样的
查询参数，否则 segment 指向的边可能不是同一组。批量项放不下筛选参数，其中的 pathway 一律按不筛重建。

---

## 常见问题

- **问**：`run_all.py` 报"必须指定 `--source=<s>` 或 `--merge`"？
  **答**：这是设计。见 §注意事项 B —— 分段流程没有"裸跑"这种模式。
- **问**：`--merge` 拒绝执行，说分段不完整？
  **答**：安全阀。先把缺的那个来源跑完（它也列出了缺哪些文件）。这是**故意**的：
  残缺汇合会覆盖掉原本完整的版本。
- **问**：`update_database.py` 报行数缩减过半？
  **答**：默认拒绝是保护。确认新数据真的是这个规模（比如换了更窄的检索词）后加 `--allow-shrink`。
- **问**：重跑后输出行数少了 4 行，正常吗？
  **答**：正常。`B5A435 / K9Y6Y9 / Q45222 / S0ENM8` 四个条目的 Rhea 注释**被上游撤回了**
  （它们仍是 Swiss-Prot，只是不再有 Rhea 交叉引用）。它们的 `enzyme` 行与编号都保留，只有边消失。
- **问**：`fetch_isoform` 对 TrEMBL 产出 0 行，是丢数据了吗？
  **答**：不是。TrEMBL 的自动注释只写了注释类型、没有 isoform 定义（含 `IsoId` 的行数为 0）。
  Swiss-Prot 段有 26 条带 `IsoId`，产出 56 行。
- **问**：ETL 看起来卡住了？
  **答**：先看日志的 mtime。机器待机也会冻结进程（不是死锁）；跑的时候加 `-u`，
  避免把"输出憋在缓冲区"误判成卡住。

---

## 许可与署名

| 部分 | 许可 | 全文 |
|---|---|---|
| 源代码（`backend/` `frontend/` `etl/` `update_tool/` `tools/` `sql/`） | Apache License 2.0 | [`LICENSE`](LICENSE) |
| 派生的表（`for_*/`）与文档（`docs/`、`.docx`、`.png`、`.json`） | CC BY 4.0 | [`LICENSE-DATA`](LICENSE-DATA) |
| 上游数据的原样再分发（`update_tool/chebi_data/`） | ChEBI 自己的条款（CC BY 4.0） | [`LICENSE-DATA`](LICENSE-DATA) |

`update_tool/` 那一行指的是它的**代码**；其下的 `chebi_data/` 装的是 EBI 的 ChEBI 数据
（流水线下载的原始压缩包，以及 `update_chebi_library.py` 从它们派生出的两张表），不是我们的作品。

上游数据（UniProt / Rhea / ChEBI / DDBJ）的署名、随附的第三方软件（Ketcher）的许可与声明、
以及 NCBI BLAST+ 与 DeepSolNet / ESM C 300M 为何**不**在本仓库内分发,都记在 [`NOTICE`](NOTICE)。
数据与文档采用 CC BY 4.0,也就是上游数据源自己的许可 —— 复用本库时请保留其中的署名。
详细说明见 [`docs/wiki/database-protocol.md`](docs/wiki/database-protocol.md) §12。
