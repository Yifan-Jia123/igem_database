"""TSV 单元格里的多值字段 -> 值列表。单一实现, 两个读取处共用。

## 契约

[build_terpene_pairs.py:65](../update_tool/build_terpene_pairs.py#L65) 把同一个酶的
多个 Rhea ID 拼进一个单元格:

    row[f'Rhea ID_{i}'] = '; '.join(sorted({r for r, _ in rxns}, key=rhea_key))

所以 `Rhea ID_{i}` 是**多值单元格**, 不是一个 ID。读它的地方必须拆开, 否则:

  - [etl_edges.py](etl_edges.py)        —— 拿整串查 reaction_map 必然 miss -> **静默丢边**
  - [etl_search_index.py](etl_search_index.py) —— 拿整串当 entity_id, 索引里多出一条
                                    永远匹配不上的假实体, 而该酶的真实反应一条都没进索引

实测现表 `for_graph/uniprotkb_terpene_pairs.tsv`: **42 个**这样的单元格,
涉及 8 行 / 36 个酶。

## 为什么放在这里

仓库里同类列已有的分隔符写法是不带空格的 `;`([etl_enzymes.py:39](etl_enzymes.py#L39)、
[etl_reactions.py:25](etl_reactions.py#L25)), 而 pairs 表用的是 `'; '`。
分隔符一旦改动, 两个读取处必须同时改 —— 写死在两处就会漂。放一处即可。
"""


def split_multi_value(value):
    """拆开 `'; '` 拼接的多值单元格。空值 / NaN 返回 []。

    调用方可以直接传 DataFrame 的原始取值: NaN 在这里一并挡掉
    (它 `!=` 自身, 不需要 import pandas —— 'nan' 那样变成字符串就会
    查表 miss, 而那正是本模块要消灭的静默丢失)。
    """
    if value is None:
        return []
    if value != value:  # NaN
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(';') if part.strip()]
