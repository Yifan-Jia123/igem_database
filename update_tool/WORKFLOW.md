# 工作流文档 — 萜烯酶数据库:新数据获取与重建

> 本文件是 `update_tool` 全新数据工作流的**完整操作与维护手册**。
> 作用:从一份 UniProt 统一导出,一键重建数据库的全部 14 张表。
> 旧表只用于**验证**,数据更新是被允许且预期的(见 §9)。

---

## 0. 文档目的

数据库的全部表(酶总表、反应表、化合物表、图节点表、引用表、序列链接表等)
原本由多个分散的 UniProt 手动导出拼接而成。本工作流把输入收敛为**一张 19 列的统一全列表**,
用一条命令重建全部输出。换新数据时只需两步:重新下载 → 重跑全流程。

设计约束(来自需求,不可违反):
- **隔离**:所有新代码与输出都放在 `update_tool/`,原始数据与代码只读、不触碰。
- **不继承**:重建时不从旧子表继承补充行,一切从原始导出重新生成。
- **新数据优先**:这是"获取新数据"的工作流,旧表仅用来检验形式是否对上。

---

## 1. 工作流总览

```
download_uniprot.py          # ① 下载 UniProt 统一 19 列全列表(网络)
      │
      ▼
output_uniprot_unified.tsv   # 唯一输入(1524 酶)
      │
      ▼
run_all.py                   # ② 一键重建 14 张表(部分步骤联网,有断点缓存)
      │
      ├─ 离线步骤: parse_names / fetch_go / build_names_split / build_rhea_summary
      │            / build_enzyme_merged / build_terpene_pairs / build_terpene_compounds
      │            / rebuild_master
      └─ 网络步骤: fetch_isoform / fetch_rhea / fetch_references / fetch_sequence_links
                   / build_terpene_only / build_all_nodes
```

---

## 2. 目录与隔离原则

| 路径 | 角色 | 读写 |
|---|---|---|
| `update_tool/` | 新工作流代码 + 全部输出 | 可写 |
| `update_tool/chebi_data/` | ChEBI 参考库的新副本(update_chebi_library 维护) | 可写 |
| `../uniprotkb_terpene_AND_reviewed_true_*.tsv`(0710/0712/0716) | 旧三导出(只读参考) | 只读 |
| `../for_enzyme_detail/` 等旧表目录 | 旧表(仅验证用) | 只读 |
| `../chebi_data/` | 旧 ChEBI 参考库(仅 update_chebi_library 维护时当 `--old` 源) | 只读 |

**绝对不要修改原始文件夹里的任何文件。** 所有输出一律写 `update_tool/output_*.tsv`。

---

## 3. 快速开始

**方式一:一键启动脚本(推荐,覆盖全流程)**

```bash
cd update_tool
./run_workflow.sh --dry-run   # 先看计划,不执行
./run_workflow.sh             # 下载 -> 重建 -> 部署(自动备份),一条龙
```

Python 版等价:`python run_workflow.py [--dry-run]`。配置通过环境变量(见 §8.2)。

**方式二:手动三步**

```bash
cd update_tool

# ① 下载统一全列表(联网,约 1524 条)
python download_uniprot.py

# ② 一键重建全部 14 张表(已有输出自动跳过;加 --force 强制全重跑)
python run_all.py            # 或 python run_all.py --force

# ③ 部署到原始数据库(自动备份,可回滚)
python update_database.py --dry-run   # 先看计划
python update_database.py
```

完毕。14 张 `output_*.tsv` 生成在 `update_tool/` 下。

---

## 4. 统一输入表(output_uniprot_unified.tsv)

由 `download_uniprot.py` 从 UniProt REST **stream** 端点下载:

- 查询:`(terpene) AND reviewed:true` → **1524** 条(截至 2026-08)
- 格式:TSV,19 列 = 旧三导出(0710/0712/0716)所有维度的并集,**已验证与三导出逐格 0 差异**

| # | 列头(UniProt 字段名) | 来源维度 | 主要消费方 |
|---|---|---|---|
| 1 | Entry(accession) | 全部 | 所有表 |
| 2 | Entry Name(id) | — | 间接 |
| 3 | Protein names | 0710 | parse_names / master |
| 4 | Organism | — | 间接 |
| 5 | Gene Names (primary) | 0710 | master |
| 6 | Kinetics | 0710 | 保留维度 |
| 7 | Function [CC] | 0710 | 保留维度 |
| 8 | Rhea ID | 0712 | fetch_rhea |
| 9 | Gene Ontology (biological process) | **新增** | fetch_go(仅 BP) |
| 10 | Sequence | 0712 | master / isoform |
| 11 | Alternative sequence | 0712 | fetch_isoform |
| 12 | EC number | 0712 | fetch_rhea |
| 13 | Catalytic activity | 0712 | fetch_rhea |
| 14 | Alternative products (isoforms) | 0716 | 保留维度 |
| 15 | PubMed ID | 0710 | fetch_references |
| 16 | DOI ID | 0710 | fetch_references |
| 17 | GeneID | 0710 | fetch_sequence_links |
| 18 | Length | 0716 | master |
| 19 | Mass | 0716 | master |

**换新数据时**:重跑 `download_uniprot.py` 覆盖此文件即可(或把新下载的 TSV 换成统一表格式,
注意列头必须与上表一致;`go_p` 字段的列头显示名为 `Gene Ontology (biological process)`)。

---

## 5. 输出表清单(14 张)与旧表对应关系

| update_tool 输出 | 对应旧表 | 内容 |
|---|---|---|
| output_parsed.tsv | (中间表) | 名称解析:推荐名/替代名/EC |
| output_names_split.tsv | for_enzyme_detail/child_tables/uniprotkb_names_split.tsv | 名称拆分 |
| output_go.tsv | for_enzyme_detail/child_tables/uniprotkb_go.tsv | GO 子表(仅生物过程 BP;无 GO 酶补空占位行) |
| output_isoform.tsv | for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv | isoform 序列子表 |
| output_rhea.tsv | for_enzyme_detail/child_tables/uniprotkb_rhea.tsv | 反应子表 |
| output_references.tsv | for_enzyme_detail/child_tables/uniprotkb_references.tsv | 参考文献子表 |
| output_sequence_links.tsv | for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv | 核酸序列链接子表 |
| output_rhea_summary.tsv | for_enzyme_reation_card/uniprotkb_rhea_summary.tsv | 反应卡摘要 |
| output_enzyme_merged.tsv | for_enzyme_reation_card/uniprotkb_enzyme_merged.tsv | 酶-反应合并 |
| output_terpene_only.tsv | for_graph/uniprotkb_terpene_only.tsv | 反应-底物产物(每反应一行) |
| output_terpene_pairs.tsv | for_graph/uniprotkb_terpene_pairs.tsv | 底物→产物对 |
| output_terpene_compounds.tsv | for_compound_card/uniprotkb_terpene_compounds.tsv | 化合物卡 |
| output_all_nodes.tsv | for_graph/all_nodes.tsv | 图节点表(ChEBI ID/Name/InChI Key) |
| output_master.tsv | for_enzyme_detail/uniprotkb_master.tsv | 酶总表(每酶一行,子表横向铺开) |

---

## 6. 流水线各步明细

| 步 | 脚本 | 输入 | 输出 | 网络 | 断点缓存 |
|---|---|---|---|---|---|
| 1 | parse_names.py | RAW | output_parsed.tsv | 离线 | — |
| 2 | fetch_go.py | RAW(go_p 列) | output_go.tsv | 离线 | — |
| 3 | fetch_isoform.py | RAW | output_isoform.tsv | UniProt REST | `_isoform_cache.json` |
| 4 | fetch_rhea.py | RAW + chebi_smiles | output_rhea.tsv | Rhea SPARQL | 无 |
| 5 | fetch_references.py | RAW | output_references.tsv | UniProt REST | `_ref_cache.json` |
| 6 | fetch_sequence_links.py | RAW | output_sequence_links.tsv | UniProt REST | `_sequence_links_cache.json` |
| 7 | build_names_split.py | output_parsed | output_names_split.tsv | 离线 | — |
| 8 | build_rhea_summary.py | output_rhea + RAW | output_rhea_summary.tsv | 离线 | — |
| 9 | build_enzyme_merged.py | output_rhea + RAW | output_enzyme_merged.tsv | 离线 | — |
| 10 | build_terpene_only.py | output_rhea | output_terpene_only.tsv | Rhea SPARQL(短名) | 无 |
| 11 | build_terpene_pairs.py | output_terpene_only | output_terpene_pairs.tsv | 离线 | — |
| 12 | build_terpene_compounds.py | output_terpene_only + chebi_full | output_terpene_compounds.tsv | 离线 | — |
| 13 | build_all_nodes.py | output_terpene_only + chebi_full | output_all_nodes.tsv | PubChem(InChI Key) | `_allnodes_inchikey.json`(共享,脚本目录) |
| 14 | rebuild_master.py | staging 子表 + RAW + output_parsed | output_master.tsv | 离线 | — |

**master 的组装**(rebuild_master.py):先把 5 张子表(go/isoform/references/rhea/sequence_links)
复制进 `update_tool/child_tables/`(固定文件名),再按 Entry 横向铺开。
Canonical 序列/长度/质量、Gene Names 都从统一表直接取;推荐名/替代名来自 output_parsed。
支持两种参数形式:新 4 参(统一表)与旧 6 参(三导出),脚本按参数个数自动识别。

---

## 7. 关键业务规则(写死的不变量)

1. **Rhea 只取通用式**:一个反应在 UniProt CC 里可能挂多个 Rhea ID——应该先对比每个子应该先对比每个子表里面的内容表里面的内
   Reaction 行上的 `Xref=Rhea:RHEA:xxxxx` 是**通用式**(无向主反应),PhysiologicalDirection 行上是
   **方向特异式**。子表 Rhea ID 列 = 通用式 = 该块**第一个** `Xref=Rhea:`。
2. **Reaction SMILES 跟随 Direction,不跟随方程式文本**:底物/产物顺序按 Direction 列
   (left-to-right / right-to-left / not specified)取 Rhea 两侧;`right-to-left` 时底物取自方程右侧。
3. **GO 只存生物过程(BP),每酶至少一行**:fetch_go 直接解析统一表
   `Gene Ontology (biological process)` 列。真实行 = 2698 个 BP 词条(与旧表逐格 0 差异);
   无 BP GO 的酶(198 个)每酶补**一行空占位**(GO ID/Term/Link 全空),保证每酶都出现在表中。
   旧表个别无 GO 酶有 2~7 行空占位(历史构建按当时 API GO 引用数发空行,源数据已变),现统一每酶一行。
   不拉全方面 C/F/P。
4. **不从旧子表继承任何行**:全部由当前导出重新解析。
5. **孤儿酶是数据更新,不是错误**:S0ENM8 / B5A435 / K9Y6Y9 / Q45222 四个 reviewed 酶
   当前数据**没有催化活性注释**(无 Reaction 块),旧表里它们的反应行是历史残留,新表不包含。
6. **EC 归一化**:去 Rhea/UniProt 的 n 级占位后缀(`1.1.1.n4` → `1.1.1.`),缺失填 `--`。

---

## 8. 换新数据流程(分步)

```bash
cd update_tool

# 1) 下载新统一表(联网)
python download_uniprot.py
#   → 覆盖 output_uniprot_unified.tsv,打印行数(应与旧表接近,数据更新正常)

# 2) 强制全量重建(断点缓存如已过时,先删 _*.json)
python run_all.py --force

# 3) 验证:与旧表对比(见 §9)
#    - 期望:仅数据更新类差异
#    - 若出现整列/整表形式差异 → 检查统一表列头或脚本参数

# 4) 部署:把新输出覆盖到原始数据库对应位置(自动备份,可回滚)
python update_database.py --dry-run     # 先看计划,不写任何文件
python update_database.py               # 正式覆盖(旧表先备份到 _backup_<时间戳>/)
```

### 8.1 部署脚本 update_database.py

把 `update_tool/` 的 14 张新输出覆盖到原始数据库对应位置(映射见 §5)。安全设计:

- **列头校验**:新表与旧表列头不完全一致时不覆盖、跳过并报告(形式对不上就不动)。
- **自动备份**:覆盖前把旧表原样备份到 `update_tool/_backup_<时间戳>/`(保留相对路径)。
- **一键回滚**:`python update_database.py --restore=<备份目录>` 把备份复制回原位置。
- **dry-run**:`--dry-run` 只打印计划(行数、目标路径),不写任何文件。
- **空表保护**:新输出无数据行时跳过,不会用空表覆盖旧数据。
- **隔离测试**:`--root=<沙箱目录>` 可指向任意目录做真实覆盖演练,不碰原始数据
  (已验证:沙箱覆盖→备份→回滚→逐字节还原,全部通过)。
- 常用参数:`--target=output_master.tsv`(只更新指定表)、`--no-backup`(不备份,不推荐)。

### 8.2 一键启动脚本 run_workflow.py / run_workflow.sh

覆盖全流程 `download_uniprot.py → run_all.py --force → update_database.py` 的启动器。
配置优先级:**环境变量 > 脚本内 CONFIG 默认值**。

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `UNI_DOWNLOAD_DIR` | 下载目录(统一表落盘位置) | update_tool/ |
| `UNI_OUTPUT_DIR` | 输出目录(14 张表) | update_tool/ |
| `UNI_BACKUP_DIR` | 部署前旧表备份目录 | 空 = 自动 `_backup_<时间戳>/` |
| `UNI_TARGET_ROOT` | 部署目标根(旧表所在根) | 数据库根(../) |
| `UNI_QUERY` | UniProt 检索词 | `(terpene) AND reviewed:true` |
| `UNI_DRY_RUN` | `1/true/yes` = 只打印不执行 | 0 |
| `UNI_SKIP_DOWNLOAD` | `1` = 跳过下载,用现有统一表 | 0 |
| `UNI_SKIP_UPDATE` | `1` = 跳过部署 | 0 |

```bash
./run_workflow.sh --dry-run                     # 打印计划
UNI_SKIP_UPDATE=1 ./run_workflow.sh             # 只下载+重建, 不部署
UNI_OUTPUT_DIR=./_test_out ./run_workflow.sh    # 输出到自定义目录
```

需要联网的步骤失败时:脚本带重试;fetch_isoform/fetch_references/fetch_sequence_links/build_all_nodes
会写断点缓存,删掉半截 `output_*.tsv` 后重跑即可断点续传。fetch_rhea / build_terpene_only
(Rhea SPARQL)无缓存,失败需整体重跑该步(已带重试与限速)。

---

## 9. 验证方法:与旧表对比的预期差异

**验证目标**:形式对齐(列头、行结构一致),数据允许更新。旧表路径见 §5。

| 输出表 | 2026-08 实测基线 | 差异 | 结论 |
|---|---|---|---|
| output_references / sequence_links | 1524 行 | **0 差异** | 形式+数据全对齐 |
| output_parsed / names_split | 1524 行 | **0 差异** | 全对齐 |
| output_go | 2896 = 2698 真实 + 198 空占位 | 真实行 **0 差异**;空占位 198 vs 旧 237(旧表个别酶多行空占位,现统一每酶一行) | 全对齐 |
| output_terpene_compounds | 634 行 | **0 差异** | 全对齐 |
| output_all_nodes | 634 行 | 1 格(CHEBI:192980 InChI Key 补全) | 数据更新 |
| output_isoform | 56 行 | 52 格 = **Mass 精度变化**(如 70785→70784.5) | 数据更新 |
| output_rhea 链(4 表) | 1795 vs 1799 | 4 行 = 4 个孤儿酶残留 | 数据更新(§7.5) |
| output_terpene_pairs | 619 行 | 12 格 = 同 4 孤儿酶 | 数据更新 |
| output_master | 1524 行 | 85 格 = Mass 精度 56 + 孤儿酶 28 + SMILES 1 | 数据更新 |

凡差异都能归因到上述三类(数据精度、孤儿酶注释消失、InChI Key 补全),即为**工作流正确**。
若出现整列错位、行数级联变化、GO 出现 C/F 词条 → 是工作流回归,需排查。

---

## 10. ChEBI 参考库维护

工作流读取**工具自带**的两个 ChEBI 参考库(`update_tool/chebi_data/`,只读,不依赖数据库根):
- `chebi_smiles.tsv` → fetch_rhea(反应 SMILES)
- `chebi_full.tsv` → build_terpene_compounds / build_all_nodes(名称、分子量)
- 已实测:634 个所需化合物在内外两份库中 Name/Mass 差异 0,切换源不影响输出。

如需更新到新 ChEBI 版本(从 ChEBI FTP flat files:`compounds.tsv.gz` / `chemical_data.tsv.gz` /
`structure_registry.tsv.gz`):

```bash
python update_chebi_library.py [--old=../chebi_data/chebi_full.tsv]
```

- 输出直接写到 `update_tool/chebi_data/`(run_all.py 默认读的就是这里,更新完即生效,无需改代码)
- 带 `--old` 时:新版丢失结构数据的化合物会用旧库**人工覆盖补丁**回填(记入 `chebi_data/curation_overrides.tsv`)
- 工具搬到别处后,再维护 ChEBI 库时用 `--old` 显式指定旧库路径(默认 `../chebi_data/` 仅在原地有效)

---

## 11. 依赖与运行环境

- Python 3(脚本用 3.10 验证),需 `requests`
- Windows 下打印中文需 `PYTHONIOENCODING=utf-8`(GBK 控制台不认 unicode)
- 网络依赖:UniProt REST、Rhea SPARQL(sparql.rhea-db.org)、PubChem
- 断点缓存文件:`_isoform_cache.json` `_ref_cache.json` `_sequence_links_cache.json`
  (成功跑完自动删除)
- `_allnodes_inchikey.json` 为**共享缓存**(脚本目录,不随成功删除):所有输出目录复用已抓好的
  InChI Key,避免自定义输出目录时重复抓取;失败的查询不写缓存,下次运行自动重试

---

## 12. 常见问题(FAQ)

- **问**:重跑后输出行数少了 4 行,正常吗?
  **答**:正常。那是 4 个孤儿酶(§7.5)的历史反应残留,当前数据无催化活性注释,不再生成。
- **问**:GO 表里为什么没有"synthase activity / chloroplast"这些词条?
  **答**:旧表只存生物过程(BP),fetch_go 按统一表 `Gene Ontology (biological process)` 列只取 BP。
- **问**:master 的 Rhea ID_1 某酶为空,但旧表有?
  **答**:该酶当前导出无催化活性注释(多为孤儿酶/被注释为 Inactive),是数据更新。
- **问**:run_all 中途断网失败怎么办?
  **答**:有断点缓存的步骤删掉半截输出重跑即可续传;fetch_rhea/build_terpene_only 需整体重跑该步。
- **问**:我想手动换一张旧导出来跑,不重新下载?
  **答**:可以。只要列头是统一表 19 列格式,把路径作为 `run_all.py` 第一个参数传入:
  `python run_all.py --force my_unified.tsv`。
