"""分段来源读取入口 —— ETL 读 for_* 分段文件的唯一入口。

## 背景

`update_tool` 把 11 张表**按来源分段**落盘(`for_*/<name>.<source>.tsv`),
另外 3 张跨条目聚合的表(pairs / compounds / all_nodes)保持无后缀。
所以 ETL 每个步骤读的都是「全部分段的并集」。

每个模块各写一份路径拼接/glob, 加一个来源时漏改一处, 那一处就会**静默只读到
一个来源的数据** —— 与源侧 `source_registry.py` 要防的是同一类漂移, 只是方向相反。

## 来源列表的唯一出处

`SOURCES` 直接 import 自 `../update_tool/source_registry.py`, 这里**不复制一份**。
ETL 与它读的 `for_*` 是同一套产物、一起部署, 没有单独分发 `etl/` 的场景,
所以取不到登记表时**直接报错**比偷偷用一份本地副本安全(副本正是漂移的来源)。

## 缺文件 = 硬报错, 不是跳过

分段文件缺失时**必须报错**。静默跳过等于「这次更新悄悄少了一个来源的数据」,
而这类缺陷的表征恰恰是「跑得通」。方向的危险性不对称:
多读一个来源只是浪费, 少读一个来源是静默丢数据。
"""

import os
import re
import sys

import pandas as pd

_ETL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ETL_DIR)
from config import DATA_DIR  # noqa: E402

# 登记表始终取真实仓库里的 update_tool/ —— 沙箱只换 for_* 的位置(IGEM_DATA_DIR),
# 不换「有哪些来源」这件事, 否则沙箱里就成了另一套来源定义。
_REGISTRY_DIR = os.path.join(os.path.dirname(_ETL_DIR), 'update_tool')


def _load_sources():
    """从 update_tool/source_registry.py 取已登记来源(唯一出处)。"""
    if _REGISTRY_DIR not in sys.path:
        sys.path.insert(0, _REGISTRY_DIR)
    try:
        from source_registry import SOURCES  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 部署异常, 不是数据异常
        raise RuntimeError(
            f'取不到来源登记表 {_REGISTRY_DIR}/source_registry.py。\n'
            'ETL 需要它来确定要读哪些分段文件。不在那里复制一份来源列表 ——'
            '两份列表会漂移, 而漂移的方向正是「悄悄少读一个来源」。'
        ) from exc
    return tuple(SOURCES)


SOURCES = _load_sources()

# 登记表 docstring 写着「顺序即优先级」—— 把这句话落成一个**可执行的**映射,
# 而不是让「谁先读谁赢」散落在各个 concat 的隐式顺序里。
# 同一个 accession 出现在多个分段时(两次下载窗口跨越了升/降级), 这里的序号就是裁决依据。
SOURCE_PRIORITY = {s: i for i, s in enumerate(SOURCES)}


def segmented_path(rel_path, source):
    """for_x/y.tsv + source -> 绝对路径 for_x/y.<source>.tsv"""
    base, ext = os.path.splitext(rel_path)
    return os.path.join(DATA_DIR, f'{base}.{source}{ext}')


def segmented_paths(rel_path, only=None):
    """[(source, path), ...]; 缺文件时抛 FileNotFoundError 并列出缺哪些。

    only=<source> 时只返回该来源(供 etl_run.py --source=<s> 只重灌一个来源)。
    """
    wanted = [only] if only else list(SOURCES)
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        raise ValueError(f'未登记的来源 {unknown}; 已登记: {list(SOURCES)}')

    pairs = [(s, segmented_path(rel_path, s)) for s in wanted]
    missing = [(s, p) for s, p in pairs if not os.path.exists(p)]
    if missing:
        # 不能跳过 —— 少读一个来源就是静默丢数据。见模块 docstring。
        lines = '\n'.join(f'  缺 {s}: {p}' for s, p in missing)
        raise FileNotFoundError(
            f'分段文件不齐, 拒绝继续: {rel_path}\n{lines}\n'
            f'  已登记来源: {list(SOURCES)}\n'
            f'  先跑 update_tool: python run_all.py --source=<s> 然后 update_database.py'
        )
    return pairs


def read_segmented(rel_path, only=None, **kwargs):
    """读全部分段并 concat。每行自带 `Source` 列(源侧写入), 这里顺带校验它。

    校验的意义: 文件名后缀与实际 `Source` 值不一致, 说明文件被部署到了错误的后缀下
    (或源侧写错了), 那会导致按来源替换时**删错/留错行** —— 静默且破坏性。
    """
    frames = []
    for source, path in segmented_paths(rel_path, only=only):
        df = pd.read_csv(path, sep='\t', **kwargs)
        if 'Source' in df.columns:
            found = {str(v).strip() for v in df['Source'].dropna().unique()}
            found.discard('')
            wrong = found - {source}
            if wrong:
                raise ValueError(
                    f'{path}\n  文件名后缀是 {source!r}, 但 Source 列里出现了 {sorted(wrong)}。\n'
                    '  文件被部署到了错误的后缀下 —— 按来源替换会删错行, 拒绝继续。'
                )
        # 归一化: 列缺失或行内为空都补成文件名对应的来源。
        # 下游一律以 `Source` 列为准(不再从文件名猜), 所以它不能有空值 ——
        # 空值会让某行归属不了来源, 按来源替换时被漏删或漏留。
        df['Source'] = source
        frames.append(df)

    if not frames:
        raise RuntimeError(f'没有读到任何分段: {rel_path}')
    if len(frames) == 1:
        return frames[0]
    return pd.concat(frames, ignore_index=True)


def columns_of(rel_path, only=None):
    """分段文件列头的交集式检查用: 每个来源的列宽可能不同(SP master 141 列 / TrEMBL 473 列)。

    返回 {source: [列名]}, 只读表头(nrows=0), 代价很小。
    """
    return {s: list(pd.read_csv(p, sep='\t', nrows=0).columns)
            for s, p in segmented_paths(rel_path, only=only)}


def safe_usecols(rel_path, wanted, only=None):
    """wanted 在所有分段里都存在才返回它, 否则返回 None。

    直接给 usecols 会在缺列的来源上直接报错 —— 而列宽恰恰是各来源不同的地方,
    所以这里必须先检查再决定。
    """
    cols = columns_of(rel_path, only=only)
    for source, names in cols.items():
        missing = [c for c in wanted if c not in names]
        if missing:
            print(f'  [usecols] {source} 段缺 {missing}, 本步退回全列读取')
            return None
    return list(wanted)


def read_merged(rel_path, **kwargs):
    """无后缀的汇合表(pairs / compounds / all_nodes)。它们与来源无关。"""
    path = os.path.join(DATA_DIR, rel_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f'汇合表不存在: {path} (先跑 python run_all.py --merge)')
    return pd.read_csv(path, sep='\t', **kwargs)


# ---- 动态列宽 ---------------------------------------------------------------
#
# `INSDC_Nuc_ID_1..N` / `Enzyme_1..N` / `PMID_1..N` 这类列族的**列数跟着数据走**。
# 把上限写死在代码里就是「静默丢数据」: 超出的那部分不会报错, 只是不存在。
# 实测三处上限**都已被顶满且有真实数据**在最后一列 ——
#   etl_edges `range(1, 46)`      -> 已提交 pairs 表的 max_n 恰好 = 45
#   etl_search_index `range(1, 25)`/`range(1, 12)` -> 最后一列各有 1 条真实数据
#   etl_master 同样两处            -> 同上
# 所以一律按列头推导。这里放**唯一一份**实现, 免得几个模块各写一份再漂移。

def indexed_groups(columns, stem):
    """列族 `X_1..X_N` -> range(1, N+1)。没有该列族时是空 range。"""
    numbers = []
    pattern = re.compile(rf'^{re.escape(stem)}_(\d+)$')
    for col in columns:
        m = pattern.match(col)
        if m:
            numbers.append(int(m.group(1)))
    return range(1, max(numbers, default=0) + 1)


def indexed_width(columns, *stems):
    """这些列族里最大的 N。同一段可能只有 prot id 没有 nuc id, 所以要取多个列族的最大值。"""
    return max((max(indexed_groups(columns, stem), default=0) for stem in stems), default=0)
