"""按稳定身份 upsert 的小工具。B 类表(全局字典, 与来源无关)共用。

方案「只补充 + 只新增」的规则: 命中已有身份 -> **原地更新**, 未命中 -> 新增。
绝不能「读现有 -> 过滤掉已存在的」再 append —— 那是「已存在就跳过」,
已有行的字段改善(smiles / equation 等)永远进不来, 而
`update_tool/backfill_etl_gaps.py` 正是为这个缺陷打的补丁。
"""
import os
import re
import sys

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# schema.sql 从**本仓库**取, 不跟着 IGEM_DATA_DIR 走 —— 那个环境变量是给沙箱换
# for_* 数据目录用的, 而建表语句属于代码, 沙箱里不该出现第二份(会漂移)。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_SQL = os.path.join(_REPO_ROOT, 'sql', 'schema.sql')

_MISSING_DDL = {}


def schema_ddl(table):
    """从 sql/schema.sql 取某张表的 CREATE TABLE 语句 —— 建表语句的唯一出处。

    ETL 里有几个模块原先各自内嵌一份 DDL 副本(`gene_sequence_link` / `enzyme_go` /
    `enzyme_isoform` / `search_index`), 与 schema.sql 重复定义。改 schema 时漏改一处,
    本地建表语句就和 schema.sql 漂移 —— 而漂移的表结构不会报错, 只会让下游读到空列。
    """
    if table in _MISSING_DDL:
        raise KeyError(f'sql/schema.sql 里找不到 {table} 的建表语句')
    if not os.path.exists(SCHEMA_SQL):
        raise FileNotFoundError(f'找不到 {SCHEMA_SQL}')
    with open(SCHEMA_SQL, encoding='utf-8') as f:
        sql = f.read()
    m = re.search(rf'CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\(.*?\n\)\s*ENGINE=[^;]*;',
                  sql, re.S)
    if not m:
        # 记下来, 免得每张表都重读一遍这个 400 行的文件。
        _MISSING_DDL[table] = True
        raise KeyError(f'sql/schema.sql 里找不到 {table} 的建表语句')
    return m.group(0)

# MySQL 的 VALUES() 在新版本被标记为 deprecated, 但在 8.0.46 上仍是可用的标准写法,
# 且比 8.0.19+ 的别名语法兼容性更好(别名语法在 MariaDB 上不认)。
def upsert_dataframe(conn, table, df, update_cols, preserve=()):
    """INSERT ... ON DUPLICATE KEY UPDATE, 冲突键由表上的 PK/UNIQUE 决定。

    update_cols: 冲突时要更新的列
    preserve:    这些列用 COALESCE(VALUES(c), c) —— 传 NULL 不会把现值抹掉。
                 给「本次数据没带、但已有值仍有效」的列(如 compound.inchi_key)。

    ⚠️ **返回值是输入行数(`len(df)`), 不是实际写入行数** —— 不是 `cursor.rowcount`。
    两条理由: (1) MySQL 对 ON DUPLICATE KEY UPDATE 的 rowcount 把「更新」按 2 计,
    拿它当行数会翻倍; (2) INSERT IGNORE 时 rowcount 会把冲突忽略的行排除掉, 于是
    `update_cols=[]`(纯只新增)在**幂等重跑**时返回 0 —— 而调用点全都拿它当
    「本次处理了多少行」打印。所以打印时必须写成「输入 N 行」而不是「新增 N 行」,
    实际是否新增要看表行数变化(2026-09-19 曾因此把「新增 0 行」误报成「新增 112 行」)。
    """
    if df.empty:
        return 0
    cols = list(df.columns)
    placeholders = ', '.join(f':{c}' for c in cols)
    col_list = ', '.join(cols)

    if update_cols:
        sets = ', '.join(
            f'{c} = COALESCE(VALUES({c}), {c})' if c in preserve else f'{c} = VALUES({c})'
            for c in update_cols
        )
        sql = f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {sets}'
    else:
        # 没有要更新的列 = 纯「只新增」, 冲突就忽略。
        sql = f'INSERT IGNORE INTO {table} ({col_list}) VALUES ({placeholders})'

    # NaN -> None: 否则 pandas 会把 NaN 当浮点数写进 VARCHAR 列。
    # 先 astype(object) 避免 numpy 标量类型(pymysql 对 numpy.int64 会报错)。
    params = df.astype(object).where(pd.notna(df), None).to_dict('records')
    conn.execute(text(sql), params)
    return len(params)
