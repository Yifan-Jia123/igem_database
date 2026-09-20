"""UniProt 'Protein names' 列 -> 推荐名。单一实现, 三处共用。

## 为什么必须共用

parse_names.py / build_rhea_summary.py / build_enzyme_merged.py 各自内联过一份切分逻辑,
**行为还不一样**(只有 parse_names 去掉了 `[Includes: ...]`)。改了其中一份, 另两份不会跟着变,
于是同一列在不同输出表里切出不同的名字 —— 而 master 会把这几张表并排放, 差异就固化成矛盾。

## 为什么不能简单切在第一个 '('

大量萜类酶名以立体化学前缀开头: `(+)-`、`(-)-`、`(E)-`、`(E,E)-`、`(2E,6E)-`、`(4S)-`、`(±)-`。
直接切第一个 '(' 会把这些名字切成空串或一个 `+`。实测 TrEMBL 切片 4727 条里 **1.9% 中招**,
且 TrEMBL 自动注释名大量是这种形态, 全量下会大面积空白。

判据:
  1. 优先切在 `(EC x.x.x.x` 前 —— 这样 `(+)-delta-cadinene synthase (EC 4.2.3.13)` 能正确保留前缀。
  2. 没有 EC 时, 切在第一个**前面有空白**的 `(` 前, 且跳过立体化学前缀。
     空格这个条件区分了两种括号: `... ntnL (FMN reductase)` 的括号是别名分隔符;
     `NAD(P)H-dependent` 里的括号是化学式的一部分, 切了会得到 `NAD`。
  3. 兜底: 若切出来还是空串而原串非空, 直接返回原串。宁可名字带点杂质,
     也不能让「有名字」变成「没名字」。
"""

import re

# 立体化学描述符: -, +, ± / E, Z, R, S 及其组合与编号 (4S, 2E,6E, 3R:5R)
_STEREOCHEM = re.compile(r'^[\-+±]$|^\d*[ERSZ]([,:]\d*[ERSZ])*$')
# 名字分隔括号 = **前面有空白**的 '(' 。括号组最长 20 字符。
# 空格这个条件很关键, 它把两种括号区分开:
#   'Squalene synthase (SQS)'      -> 空格 + '(' = 别名分隔符, 可以切
#   'NAD(P)H-dependent reductase'  -> 前面是字母 = 化学式的一部分, 不能切
# 只用「第一个 '('」会把后者切成 'NAD'。
_SEP_PAREN = re.compile(r'\s\(([^()]{1,20})\)')
# '(EC 1.1.1.1' —— 只匹配到数字, 后面的 ')' 可有可无
_EC_SPLIT = re.compile(r'\s*\(EC\s+\d+\.\d+\.\d+')
_INCLUDES = re.compile(r'\s*\[Includes:.*')


def _first_name_separator(raw):
    """返回第一个「不是立体化学前缀」的别名分隔括号的起始位置, 没有则 -1。"""
    for m in _SEP_PAREN.finditer(raw):
        # 立体化学前缀本身是名字的一部分 ((+)-delta-cadinene synthase),
        # 不能当成别名分隔符切掉, 否则前缀会丢。
        if _STEREOCHEM.match(m.group(1)):
            continue
        return m.start()
    return -1


def split_recommended_name(raw):
    """'Protein names' 原始串 -> 推荐名。raw 为空时返回空串。"""
    if not raw:
        return ''

    # 1) 优先切在 (EC x.x.x.x) 前 —— 有 EC 时这条最可靠
    parts = _EC_SPLIT.split(raw, maxsplit=1)
    if len(parts) > 1:
        rec = parts[0]
    else:
        # 2) 无 EC: 切在第一个真正是别名分隔符的括号前
        cut = _first_name_separator(raw)
        rec = raw[:cut] if cut > 0 else raw

    rec = _INCLUDES.sub('', rec).strip().rstrip(',').rstrip()

    # 3) 兜底: 切没了就退回原串的合理形式。
    #    宁可名字带点杂质, 也不能让「有名字」变成「没名字」。
    if not rec:
        rec = _INCLUDES.sub('', raw).strip().rstrip(',').rstrip()
    return rec
