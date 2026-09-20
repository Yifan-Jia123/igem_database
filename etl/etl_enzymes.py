"""ETL Step 2: 酶表 —— 按来源替换 + 编号永久保留不复用。

## 两条不变量 (方案 Phase 2.1 / 2.2)

1. **编号永久保留不复用**。原实现是位置编号 `f"ENZ{i+1:06d}"` —— 基础集合一变
   (996 -> 96k) 全库错位, 而 `enzyme_id` 被 **7 张表的 FK** 引用。
   现在编号一律来自 `enzyme_id_map`: 命中沿用 / 未命中取 `MAX+1` / 条目消失只打
   `retired_at` 而**不回收编号**。任何已发出的 ENZ 号永远指同一个生物学实体。

2. **只补充 + 只新增**。按来源整段替换(`DELETE WHERE source_type=<s>` 后重灌),
   **不是**「读现有 -> 过滤掉已存在的」。后者是「已存在就跳过」: 已有酶的新名字、
   新 GO 永远进不来 —— `update_tool/backfill_etl_gaps.py` 就是为这个缺陷打的补丁。

## 基础集合 = names_split(全部条目), 不是 rhea_summary

原实现从 `uniprotkb_rhea_summary.tsv` 取酶 —— 那是「有反应的酶」。
只有序列没有反应注释的 528 个 SwissProt 酶因此被**静默丢弃**。
names_split 含全部条目。

⚠️ **不能换成 `uniprotkb_master.tsv`**: 它 951 列但**没有 `Organism` / `Protein name`**, 会 KeyError。

## 子表必须先清 (调用顺序约束)

`enzyme` 的子表(gene / gene_sequence_link / evidence / enzyme_go / enzyme_isoform /
enzyme_reaction_edge / search_index)**都没有 `ON DELETE CASCADE`**。
所以本模块的 `DELETE FROM enzyme` 必须发生在 etl_run.py 的 purge 阶段**之后**,
否则直接外键报错。单独跑本模块前必须已 purge。
"""
import json
import os
import re
import sys

import pandas as pd
import requests
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL  # noqa: E402
from sources import SOURCE_PRIORITY, read_segmented  # noqa: E402

engine = create_engine(DB_URL)

NAMES_FILE = 'for_enzyme_detail/child_tables/uniprotkb_names_split.tsv'
RHEA_SUMMARY_FILE = 'for_enzyme_reation_card/uniprotkb_rhea_summary.tsv'

# 来源 -> review_status。写成映射而不是 if/else, 未知来源直接报错 ——
# 「猜一个默认值」会让新来源的审核状态静默变成错的。
SOURCE_TO_REVIEW = {'swiss_prot': 'official', 'trembl': 'pending'}

# 已消失 accession 的 302 解析上限。见 _resolve_redirects。
REDIRECT_LIMIT = 500
_ACC_IN_LOCATION = re.compile(r'/uniprotkb/([A-Za-z0-9]+)')


def _coalesce(*series):
    """按顺序取第一个非空值 —— **把空串也当空**。

    原实现用 `fillna`, 而 `fillna` 只补 NaN、**不补空串**。TrEMBL 的名字存在
    空串形态, 于是 primary_name 会落成空串并绕过所有兜底, 变成没有名字的酶。
    """
    out = None
    for s in series:
        cur = s.fillna('').astype(str).str.strip()
        out = cur if out is None else out.where(out != '', cur)
    return out


def load_entries(only=None):
    """names_split 为基集(含全部条目), 左连 rhea_summary 取 Protein name / Organism。

    names_split 一行一条目; rhea_summary 一行一反应 -> 先按条目收敛再连。
    """
    names = read_segmented(NAMES_FILE, only=only, dtype=str)
    rhea = read_segmented(RHEA_SUMMARY_FILE, only=only, dtype=str)

    for col in ('Entry', 'Recommended name', 'Source'):
        if col not in names.columns:
            raise KeyError(f'{NAMES_FILE} 缺列 {col!r}; 实际列: {list(names.columns)}')
    if 'Protein name' not in rhea.columns:
        raise KeyError(f'{RHEA_SUMMARY_FILE} 缺列 "Protein name"')

    # ⚠️ 这里**不能**按 Entry 静默去重。原来这里有一句 `drop_duplicates(subset='Entry')`,
    # 它把「同一 accession 出现在两个分段」的情况就地抹平了 —— 留下的是**先读到的那一段**
    # (即 SOURCES 的顺序), 不打印任何东西。结果恰好与「SwissProt 优先」一致, 所以一直没暴露;
    # 但它同时让 dedupe_cross_source 的显式优先级与冲突报告成了永不执行的死代码,
    # 于是同一条规则有了两份实现, 而真正生效的那份是靠 concat 顺序隐式表达的。
    # 现在一律交给 dedupe_cross_source 裁: 显式优先级 + 逐条打印。
    rhea_by_entry = rhea.drop_duplicates(subset='Entry').set_index('Entry')

    entry = names['Entry'].astype(str).str.strip()

    def _from_rhea(col):
        if col not in rhea_by_entry.columns:
            return pd.Series([''] * len(entry), index=entry.index)
        return pd.Series(rhea_by_entry[col].reindex(entry).values, index=entry.index)

    source = names['Source'].astype(str).str.strip()
    review = source.map(SOURCE_TO_REVIEW)
    if review.isna().any():
        bad = sorted(set(source[review.isna()]))
        raise ValueError(
            f'未知来源 {bad}: 无法确定 review_status。已登记映射 {SOURCE_TO_REVIEW}。\n'
            '  新增来源时要在 SOURCE_TO_REVIEW 里补上, 不要让它落到默认值。'
        )

    organism = _coalesce(names.get('Organism', pd.Series([''] * len(entry), index=entry.index)),
                         _from_rhea('Organism'))

    enzyme = pd.DataFrame({
        'uniprot_id': entry.values,
        'primary_name': _coalesce(names.get('Recommended name',
                                            pd.Series([''] * len(entry), index=entry.index)),
                                  _from_rhea('Protein name'),
                                  entry).values,
        'organism_name': organism.values,
        'secondary_names': names.get('Alternative names',
                                     pd.Series([''] * len(entry), index=entry.index)).values,
        'source_type': source.values,
        'review_status': review.values,
    })
    enzyme = enzyme[enzyme['uniprot_id'] != ''].reset_index(drop=True)
    return enzyme


def dedupe_cross_source(enzyme):
    """同一 accession 同时出现在两个分段时, 高优先级来源胜出, 另一段的行剔除。

    否则 `enzyme.uniprot_id` 的 UNIQUE 会直接报错。reviewed 状态变化(升级/降级)
    在两段下载窗口之间发生, 正是这种情况的来源 —— 同一个 accession 就是一个实体,
    所以**剔除而不是各留一行**, 并且剔除的是低优先级来源那份。

    优先级取自 `source_registry.SOURCES` 的顺序(经 sources.SOURCE_PRIORITY),
    **不在这里另写一份** —— 两份会在加来源时漂移, 而漂移的表现是「静默按错的那份裁决」。

    这条路径是**唯一**的裁决点: 上游 load_entries 不再按 Entry 预去重, 所以这里必然会看到
    重复行, 冲突也必然会被打印出来(见 load_entries 的注释)。
    """
    dup = enzyme[enzyme.duplicated(subset='uniprot_id', keep=False)]
    if dup.empty:
        return enzyme, 0

    enzyme = enzyme.copy()
    enzyme['_prio'] = enzyme['source_type'].map(SOURCE_PRIORITY)
    # 原来的写法是 `.fillna(99)` —— 未知来源静默排到最后。宁可报错: 一个没登记进
    # SOURCES 的来源值本来就取不到(分段路径按登记表校验), 出现即意味着有东西绕过了它。
    if enzyme['_prio'].isna().any():
        bad = sorted(set(enzyme.loc[enzyme['_prio'].isna(), 'source_type']))
        raise ValueError(f'未知来源 {bad}: 无法裁决跨来源冲突。已登记 {list(SOURCE_PRIORITY)}。')
    enzyme = enzyme.sort_values(['uniprot_id', '_prio'], kind='stable')
    kept = enzyme.drop_duplicates(subset='uniprot_id', keep='first').drop(columns='_prio')
    dropped = len(enzyme) - len(kept)

    losers = enzyme[enzyme.duplicated(subset='uniprot_id', keep='first')]
    if not losers.empty:
        for acc, grp in losers.groupby('uniprot_id'):
            print(f'  [来源冲突] {acc}: 保留 {kept.loc[kept.uniprot_id == acc, "source_type"].iloc[0]}, '
                  f'剔除 {"+".join(sorted(set(grp.source_type)))}')
    print(f'  enzyme: 跨来源重复 accession {len(dup)} 行, 剔除 {dropped} 行 (SwissProt 优先)')
    return kept.reset_index(drop=True), dropped


def load_id_maps():
    """enzyme_id_map / enzyme_alias_map -> 两个 dict。这两张表永不参与 DELETE。"""
    with engine.connect() as conn:
        m = pd.read_sql("SELECT uniprot_id, enzyme_id FROM enzyme_id_map", conn)
        a = pd.read_sql("SELECT secondary_accession, enzyme_id FROM enzyme_alias_map", conn)
    id_map = {str(k).strip(): str(v).strip() for k, v in zip(m.uniprot_id, m.enzyme_id)}
    alias_map = {str(k).strip(): str(v).strip() for k, v in zip(a.secondary_accession, a.enzyme_id)}
    return id_map, alias_map


def _resolve_redirect(acc):
    """UniProt 对已退休 accession 返回 302 到当前主 accession。取不到返回 None。

    实测 4/4 一跳到位 (例: B6SYF3 -> A0A1D6EFT8)。
    """
    try:
        r = requests.get(f'https://rest.uniprot.org/uniprotkb/{acc}',
                         allow_redirects=False, timeout=15)
    except Exception:
        return None
    if r.status_code not in (301, 302, 303, 307, 308):
        return None
    m = _ACC_IN_LOCATION.search(r.headers.get('Location', ''))
    return m.group(1) if m else None


def resolve_vanished(id_map, present_accessions, only=None):
    """给「以前有编号、这次数据里不见了」的 accession 找它的新号。

    这里**只在消失的编号上发请求**(≤ |编号表|, 实际极小), 而不是对每个匹配失败的
    新条目发请求 —— 后者在第一次全量扩容时等于 94k 次 HTTP 请求(约 5 小时)。
    方向反过来问「我们认识的那个号还在不在」, 既够用又有界。

    命中新号且新号在当前数据里 -> 写 enzyme_alias_map, 后续沿用原编号。
    """
    if only:
        # 只重灌一个来源时, 另一个来源的条目仍在库中, 无法判定「消失」。
        print('  [redirect] --source 模式跳过退休号解析 (需全量视图)')
        return 0

    vanished = sorted(set(id_map) - present_accessions)
    if not vanished:
        return 0
    if len(vanished) > REDIRECT_LIMIT:
        print(f'  [redirect] 消失的编号有 {len(vanished)} 个, 超过上限 {REDIRECT_LIMIT}, '
              f'本步跳过 (逐个发 HTTP 请求代价过高)。编号仍会保留, 只是不接续。')
        return 0

    print(f'  [redirect] {len(vanished)} 个编号对应的 accession 已不在数据里, 逐个解析 302 ...')
    resolved = 0
    for acc in vanished:
        new_acc = _resolve_redirect(acc)
        if not new_acc or new_acc == acc:
            continue
        if new_acc in present_accessions:
            print(f'    {acc} -> {new_acc} (编号接续)')
            resolved += 1
        else:
            print(f'    {acc} -> {new_acc} (新号也不在数据里, 视为退休)')
    return resolved


def assign_ids(enzyme, id_map, alias_map):
    """分配 enzyme_id。命中沿用 / secondary 命中沿用 / 未命中取 MAX+1。**绝不填补空洞**。"""
    used = set(id_map.values())
    nxt = 1 + max((int(e[3:]) for e in used
                   if e.startswith('ENZ') and e[3:].isdigit()), default=0)

    ids, new_rows = [], []
    reused = by_alias = allocated = 0
    for acc in enzyme['uniprot_id']:
        eid = id_map.get(acc)
        if eid:
            reused += 1
        else:
            eid = alias_map.get(acc)
            if eid:
                by_alias += 1
            else:
                eid = f'ENZ{nxt:06d}'
                nxt += 1
                allocated += 1
            new_rows.append((acc, eid))     # 别名接续的也要补主号映射
        ids.append(eid)

    # 新分配的号段是 [nxt - allocated, nxt - 1] —— 下面那句断言用的也是 nxt - allocated,
    # 早先这里多写了个 +1, 日志于是显示「从 ENZ000002 起」, 与库里实际的起始号对不上。
    print(f'  enzyme_id: 沿用 {reused}, 经别名接续 {by_alias}, 新分配 {allocated}'
          f'{f" (从 ENZ{nxt - allocated:06d} 到 ENZ{nxt - 1:06d})" if allocated else ""}')
    if allocated and (nxt - allocated) <= max((int(e[3:]) for e in used
                                              if e.startswith('ENZ') and e[3:].isdigit()), default=0):
        raise AssertionError('新分配的编号没有大于历史 MAX —— 违反「编号只增不减」')
    return ids, new_rows


def write_id_map(new_rows, present_accessions, only=None):
    """回写编号表 + 给本次消失的条目打 retired_at(不删行, 编号不回收)。"""
    if new_rows:
        with engine.begin() as conn:
            conn.execute(
                text('INSERT INTO enzyme_id_map (uniprot_id, enzyme_id, first_seen) '
                     'VALUES (:u, :e, NOW()) '
                     'ON DUPLICATE KEY UPDATE enzyme_id = VALUES(enzyme_id), retired_at = NULL'),
                [{'u': u, 'e': e} for u, e in new_rows],
            )
        print(f'  enzyme_id_map: 新增 {len(new_rows)} 行')

    if only:
        # 与 resolve_vanished 同理: 只重灌一个来源时 present_accessions 只含该来源,
        # 另一个来源的条目一条都没进这一眼, 会被整批误判成「从数据里消失了」——
        # 而它们仍在库里、编号照用。判「消失」必须站在全量视图上, 所以整步跳过。
        # 实测(2026-09-19): 只跑 --source=trembl 会给全部 1,535 个 swiss_prot 条目
        # 打上 retired_at(其中 1,535 条当时都还在 enzyme 表里)。全量重跑虽然会把它们
        # 清回来, 但「单来源刷新不得改动另一个来源的任何一行」要求这里根本别碰。
        print(f'  enzyme_id_map: --source={only} 模式跳过 retired_at 维护 (需全量视图)')
        return

    with engine.connect() as conn:
        known = [r[0] for r in conn.execute(text('SELECT uniprot_id FROM enzyme_id_map'))]

    gone = [a for a in known if a not in present_accessions]
    back = [a for a in known if a in present_accessions]

    with engine.begin() as conn:
        if gone:
            conn.execute(text(_retire_sql(gone, True)),
                         {f'p{i}': a for i, a in enumerate(gone)})
        if back:
            # 条目回来了(降级后又升格): 撤掉退休标记。
            conn.execute(text(_retire_sql(back, False)),
                         {f'p{i}': a for i, a in enumerate(back)})

    if gone:
        print(f'  enzyme_id_map: {len(gone)} 个条目消失 -> 打 retired_at (编号保留, 不回收)')
    if back:
        print(f'  enzyme_id_map: {len(back)} 个条目回来 -> 撤掉 retired_at')


def _retire_sql(accessions, retire):
    """IN 列表不能直接用绑定参数, 按长度展开占位符 (IN 不接受一个列表参数)。"""
    placeholders = ','.join(f':p{i}' for i in range(len(accessions)))
    if retire:
        return ('UPDATE enzyme_id_map SET retired_at = NOW() '
                f'WHERE retired_at IS NULL AND uniprot_id IN ({placeholders})')
    return ('UPDATE enzyme_id_map SET retired_at = NULL '
            f'WHERE retired_at IS NOT NULL AND uniprot_id IN ({placeholders})')


def load_enzymes(only=None):
    """按来源替换酶表。调用前子表必须已由 etl_run.purge_source() 清空。"""
    enzyme = load_entries(only=only)
    print(f'  enzyme: 读入 {len(enzyme)} 条目 (基集 = names_split)')
    enzyme, _ = dedupe_cross_source(enzyme)

    id_map, alias_map = load_id_maps()
    print(f'  enzyme_id_map: {len(id_map)} 行, enzyme_alias_map: {len(alias_map)} 行')

    present = set(enzyme['uniprot_id'])
    resolve_vanished(id_map, present, only=only)

    ids, new_rows = assign_ids(enzyme, id_map, alias_map)
    enzyme['enzyme_id'] = ids
    enzyme['secondary_names'] = enzyme['secondary_names'].fillna('').apply(
        lambda s: json.dumps([x.strip() for x in str(s).split(';') if x.strip()])
    )

    sources = sorted(set(enzyme['source_type']))
    cols = ['enzyme_id', 'uniprot_id', 'primary_name', 'organism_name',
            'secondary_names', 'source_type', 'review_status']

    with engine.begin() as conn:
        for s in sources:
            n = conn.execute(text('DELETE FROM enzyme WHERE source_type = :s'), {'s': s}).rowcount
            print(f'  enzyme: 删除 source_type={s} 的 {n} 行 (整段替换)')
        enzyme[cols].to_sql('enzyme', conn, if_exists='append', index=False)

    print(f'  enzyme: 插入 {len(enzyme)} 行')
    for s in sources:
        print(f'     {s}: {int((enzyme.source_type == s).sum())}')
    write_id_map(new_rows, present, only=only)


if __name__ == '__main__':
    load_enzymes()


def run(only=None):
    load_enzymes(only=only)
