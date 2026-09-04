# 前端界面与人机交互评审

评审对象：`frontend/` React + Vite 前端

评审日期：2026-09-01

评审视角：以科研用户完成“发现数据 → 检索/筛选 → 理解关系 → 核验详情 → 加入队列 → 导出”的完整任务为主线，结合可发现性、反馈与状态、错误恢复、可访问性、响应式和术语一致性进行检查。

评审方式：源码、DOM 语义、交互状态和 CSS 规则检查，并执行生产构建验证。本次没有连接真实后端数据进行浏览器端可用性测试，因此视觉对比度和真实数据下的布局表现应在后续走查中复核。

## 总体结论

当前前端已经形成较完整的产品骨架：有首页关系图、搜索库、详情页、同源检索入口、下载队列、化合物结构图、酶序列和证据链接；关键异步流程也有加载和错误状态。对于熟悉数据库的用户，主要路径是可理解的。

但目前更像“功能演示版”而不是稳定的科研工作台。最大问题不是视觉风格，而是界面承诺与实际行为不完全一致：用户看到的下载格式、列选择、图谱控制和队列状态，有一部分没有真正改变结果；多个失败状态只能被动告知，用户没有重试或回退路径；首页图谱的核心对象缺少可用的键盘语义。这些问题会损害数据导出可信度，也会让第一次使用的用户不知道下一步应该怎么做。

建议优先修复 P0/P1 项后，再做视觉细化和更大范围的可用性测试。

## 按优先级汇总

| 编号 | 优先级 | 问题 | 对用户任务的影响 |
| --- | --- | --- | --- |
| HCI-01 | P0 | 下载格式和自定义列只是展示，实际始终导出固定 CSV | 用户可能误以为拿到了 FASTA/TSV/XLSX，造成数据使用错误 |
| HCI-02 | P1 | 搜索结果中的队列按钮始终显示“Add to queue”，但实际是 toggle | 用户无法判断条目是否已加入队列，可能误删或重复操作 |
| HCI-03 | P1 | 首页图谱节点和边主要依赖鼠标/指针，缺少键盘操作与可读对象名称 | 键盘用户、屏幕阅读器用户无法完成核心图谱探索 |
| HCI-04 | P1 | 数据加载/搜索失败没有明确的重试入口，部分后端失败会静默回退到 mock 数据 | 用户无法确认当前看到的是实时数据还是演示数据，也无法自行恢复 |
| HCI-05 | P1 | 当前选择不会随着筛选/查询结果同步清除，右侧可能展示已不在当前结果中的记录 | 结果列表、详情面板和用户认知不一致，增加核验风险 |
| HCI-06 | P1 | BLAST/同源检索只显示笼统的 Searching，没有进度、取消和结果状态解释 | 长耗时科研任务缺少可预期性，用户容易重复提交或误以为卡死 |
| HCI-07 | P2 | 首页和内页品牌/导航语言不一致，首页没有显式的搜索、返回或工作区导航 | 用户在首页与内页之间切换时缺少位置感和方向感 |
| HCI-08 | P2 | 反馈信息没有统一使用 `role="status"`/`aria-live`，复制序列也没有成功反馈 | 操作成功、失败和异步完成对辅助技术用户不可感知，对普通用户也缺少确认 |
| HCI-09 | P2 | 清空队列没有确认或撤销，队列初始带有预置条目 | 破坏性操作缺少保护；首次打开时用户可能误以为这些是自己保存的记录 |
| HCI-10 | P2 | 图谱、详情和下载页在响应式下大量依赖固定定位/固定高度，核心浮层可能遮挡内容 | 小屏和窄屏下，图谱与详情卡的浏览、关闭和回看成本升高 |

优先级含义：P0 表示会直接产生错误或不可信输出；P1 表示会阻断主要任务或造成明显误解；P2 表示效率、可访问性、一致性或可恢复性问题。

## 详细发现

### HCI-01：导出设置与实际输出不一致（P0）

下载页提供 `FASTA`、`TSV`、`TXT`、`XLSX` 四种格式按钮，以及 `ID`、`Name`、`Species` 等自定义列按钮，但每个格式按钮都直接调用同一个 `exportQueue()`，该函数固定生成 `text/csv` 并下载 `terpene-atlas-download-queue.csv`。自定义列按钮没有状态，也没有 `onClick`。

证据：

- [frontend/src/App.tsx:217](/D:/yaorz/works/hci/project/frontend/src/App.tsx:217)-[243](/D:/yaorz/works/hci/project/frontend/src/App.tsx:243)：导出函数固定为 CSV 和固定列。
- [frontend/src/App.tsx:848](/D:/yaorz/works/hci/project/frontend/src/App.tsx:848)-[870](/D:/yaorz/works/hci/project/frontend/src/App.tsx:870)：格式按钮都会触发 `exportQueue`，列按钮没有交互状态。
- 后端已经有下载预览/生成接口，支持 `format`、`fields` 等参数，但当前下载队列页没有使用这些设置。

用户影响：这是数据工作流中的高风险问题。用户选择 XLSX 后得到 CSV，可能在文件扩展名、工具识别和后续分析环节中产生误判；选择列后结果不变，也会破坏用户对系统的信任。

建议：

1. 把格式和列改为真正受控状态，当前选项要有明显的 selected/checked 状态和可访问名称。
2. 在下载前调用 `/download/preview`，显示文件名、行数、列数和最终格式。
3. 根据格式调用 `/download/files`，成功后显示“已生成”和“下载文件”链接；失败时保留用户配置并提供重试。
4. 对 FASTA 这种不适合所有记录类型的格式，按队列内容禁用并说明原因，而不是让按钮看似可用。

验收标准：选择 TSV、取消 Description 列后，预览列和下载文件内容都发生对应变化；选择 XLSX 后文件确实是可打开的 XLSX，而不是改名的 CSV。

### HCI-02：队列按钮状态与行为不一致（P1）

搜索页把 `toggleQueue` 作为 `addToQueue` 传给结果项，但结果项始终使用下载图标和 `title="Add to queue"`。因此，对已在队列中的条目，点击后实际上会移除它，但界面仍然告诉用户这是“加入队列”。搜索结果项本身也没有显示 queued 状态。

证据：

- [frontend/src/App.tsx:392](/D:/yaorz/works/hci/project/frontend/src/App.tsx:392)：搜索结果收到的是 toggle 行为。
- [frontend/src/App.tsx:1158](/D:/yaorz/works/hci/project/frontend/src/App.tsx:1158)-[1165](/D:/yaorz/works/hci/project/frontend/src/App.tsx:1165)：结果按钮固定显示“Add to queue”。
- 详情面板已经根据 `isQueued` 切换文案，说明队列状态可以被正确传入，当前只是结果项没有接入。

建议：给 `SearchResult` 增加 `isQueued` 参数，已加入时显示勾选/“In queue”或“Remove from queue”，并给按钮增加 `aria-pressed`。点击后提供轻量 toast 或结果项内联状态反馈。首页的 `card-check` 也应使用“Add/Remove enzyme from download queue”的可访问名称。

### HCI-03：图谱核心对象缺少完整的键盘和辅助技术语义（P1）

首页的实时图谱以 SVG 呈现，整个 SVG 被标记为 `role="img"`，但真正可以点击的 compound 圆点没有 `role="button"`、`tabIndex` 或键盘事件；可点击的关系路径也没有按钮语义。用户需要凭经验点击或拖拽节点，界面没有在图谱附近说明“点击查看详情、拖动布局、拖到边缘展开”。

证据：

- [frontend/src/graphExperience.tsx:1002](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1002)-[1013](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1013)：首页图谱只声明为图像。
- [frontend/src/graphExperience.tsx:1044](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1044)-[1053](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1053)：关系路径通过 `onClick` 交互，但没有对象语义。
- [frontend/src/graphExperience.tsx:1103](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1103)-[1113](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1103)：compound 圆点只监听 pointer 事件，`title` 只能提供有限的悬停提示。

建议：

1. 给节点和关系建立可聚焦对象；至少支持 Tab 聚焦、Enter/Space 选择、Escape 关闭详情。
2. 为每个节点提供包含名称、ChEBI ID、邻居数量的 accessible name；为每条关系提供底物、产物、酶和反应 ID 的名称。
3. 增加图谱操作说明和“以列表查看当前可见节点/边”的替代入口，避免把科研数据理解完全建立在空间图形上。
4. 将“拖动节点”和“点击选择”区分为可预测的手势，拖动结束时不要仅靠位移阈值推断用户意图。

### HCI-04：错误状态不能恢复，实时/演示数据边界不清（P1）

应用启动时加载后端数据失败会在控制台 `console.warn`，界面继续显示 mock 数据，没有向用户说明当前数据来源。首页图谱失败后仅显示错误文本，没有重试按钮；酶详情失败后也只有错误状态，没有重试或返回搜索结果的明确动作。

证据：

- [frontend/src/App.tsx:95](/D:/yaorz/works/hci/project/frontend/src/App.tsx:95)-[116](/D:/yaorz/works/hci/project/frontend/src/App.tsx:116)：后端数据失败时静默使用 mock 数据。
- [frontend/src/graphExperience.tsx:208](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:208)-[229](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:229)：首页失败只设置错误字符串。
- [frontend/src/graphExperience.tsx:1313](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1313)-[1315](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1315)：详情页显示错误但没有恢复动作。

用户影响：科研用户最关心数据是否来自当前数据库。静默回退会让“能看到页面”被误认为“实时数据正常”，尤其危险的是页面仍然显示 `Live dataset` 等实时状态文案。

建议：区分 `live`、`demo/fallback`、`offline` 三种数据状态；在页面显著位置显示来源和最后同步时间。错误状态提供“重试”“返回搜索”“使用演示数据”三个明确动作，使用演示数据时必须主动告知。

### HCI-05：筛选后详情面板可能保留不可见的旧选择（P1）

`selectedId` 是全局页面状态。搜索结果发生变化时，只有在后端返回非空结果时才尝试选择第一条；结果为空、筛选排除了当前条目或清除结果时，没有将当前选择置空。`SearchView` 仍然根据 `selectedId` 从全局实体表取详情，因此右侧可能展示不在当前列表中的记录。

证据：

- [frontend/src/App.tsx:148](/D:/yaorz/works/hci/project/frontend/src/App.tsx:148)-[155](/D:/yaorz/works/hci/project/frontend/src/App.tsx:155)：空结果时没有清理 `selectedId`。
- [frontend/src/App.tsx:674](/D:/yaorz/works/hci/project/frontend/src/App.tsx:674)：详情直接按全局 `selectedId` 取实体。
- [frontend/src/App.tsx:772](/D:/yaorz/works/hci/project/frontend/src/App.tsx:772)-[779](/D:/yaorz/works/hci/project/frontend/src/App.tsx:779)：右侧详情不会校验实体是否仍在当前过滤结果中。

建议：当当前选择不在 `filteredEntities` 中时清除选择；有结果时自动选择第一条并在列表中滚动到该项；无结果时显示空状态和“清除查询/筛选”按钮。若产品希望保留旧详情，应明确标注“当前结果之外的已选记录”。

### HCI-06：同源检索缺少长任务反馈（P1）

同源检索接口的数据结构包含 `status` 和 `progress`，但 `searchHomologyEntries` 会等待整个请求完成后才返回结果，界面只显示 `Searching backend...`。用户看不到进度、预计等待时间、任务 ID，也不能取消或重试单个任务。

证据：

- [frontend/src/api.ts:104](/D:/yaorz/works/hci/project/frontend/src/api.ts:104)-[109](/D:/yaorz/works/hci/project/frontend/src/api.ts:109)：类型中有状态和进度字段。
- [frontend/src/api.ts:115](/D:/yaorz/works/hci/project/frontend/src/api.ts:115)-[128](/D:/yaorz/works/hci/project/frontend/src/api.ts:128)：搜索函数只返回最终结果实体。
- [frontend/src/App.tsx:139](/D:/yaorz/works/hci/project/frontend/src/App.tsx:139)-[165](/D:/yaorz/works/hci/project/frontend/src/App.tsx:165)：前端只设置布尔 loading，并将失败显示为笼统提示。

建议：采用“提交任务 → 展示任务状态 → 轮询/推送结果”的交互；显示序列长度、任务状态、进度、预计下一步和取消按钮。结果表明确标注 `Identity`、`E-value` 的含义，并提供排序依据和无结果解释。

### HCI-07：信息架构和命名不一致（P2）

内页侧栏使用 `Terpene Atlas`、`Overview`、`Search library`、`Download queue`；首页却显示 `Starase Atlas`，并把下载入口命名为 `Downloading table`。首页没有同等层级的侧栏导航，酶详情通过 `Back` 返回首页，不保留用户从搜索进入详情前的路径。

证据：

- [frontend/src/App.tsx:57](/D:/yaorz/works/hci/project/frontend/src/App.tsx:57)-[61](/D:/yaorz/works/hci/project/frontend/src/App.tsx:61)：内页导航词汇。
- [frontend/src/graphExperience.tsx:852](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:852)-[865](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:865)：首页独立的品牌和工作区入口。
- [frontend/src/graphExperience.tsx:1307](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1307)-[1310](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1307)：详情页只有返回、队列和导出操作。

建议统一产品名和术语，例如统一为 `Terpene Atlas`、`Download queue`。首页至少提供可见的 Search、Download queue 和当前页面标识；使用 URL 路由或可恢复的查询状态保留搜索词、筛选项、选中实体和返回路径，使刷新、浏览器后退和分享链接可用。

### HCI-08：异步反馈和复制操作不可感知（P2）

加载、错误、扩展图谱和搜索反馈都是普通 `div`，没有 `role="status"`、`role="alert"` 或 `aria-live`。酶详情的序列 `Copy` 按钮调用剪贴板 API 后也没有成功/失败文案。

证据：

- [frontend/src/graphExperience.tsx:997](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:997)-[1000](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1000)：首页状态反馈没有 live region。
- [frontend/src/App.tsx:751](/D:/yaorz/works/hci/project/frontend/src/App.tsx:751)-[755](/D:/yaorz/works/hci/project/frontend/src/App.tsx:755)：搜索摘要和错误提示没有状态语义。
- [frontend/src/graphExperience.tsx:1367](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1367)-[1371](/D:/yaorz/works/hci/project/frontend/src/graphExperience.tsx:1371)：复制后无结果反馈。

建议：加载用 `role="status"`，错误用 `role="alert"`；反馈消息保持稳定节点而不是只在渲染时出现。复制成功显示“Sequence copied”，失败显示“无法访问剪贴板，请手动选择复制”。所有图标按钮使用 `aria-label`，不要只依赖 `title`。

### HCI-09：队列破坏性操作缺少保护（P2）

`Clear queue` 会立即清空全部队列，没有确认、撤销或恢复；队列初始状态包含两个预置 ID。对于用户而言，首次进入下载页时会看到并非自己刚刚选择的条目，容易误判为历史保存、示例数据还是系统推荐。

证据：

- [frontend/src/App.tsx:88](/D:/yaorz/works/hci/project/frontend/src/App.tsx:88)：队列初始包含预置记录。
- [frontend/src/App.tsx:208](/D:/yaorz/works/hci/project/frontend/src/App.tsx:208)-[210](/D:/yaorz/works/hci/project/frontend/src/App.tsx:210)：清空操作直接丢弃状态。
- [frontend/src/App.tsx:825](/D:/yaorz/works/hci/project/frontend/src/App.tsx:825)-[831](/D:/yaorz/works/hci/project/frontend/src/App.tsx:831)：界面提供清空按钮，但没有二次确认或撤销。

建议：生产环境不要预置用户队列；如果必须保留示例数据，显式标注“示例队列”。清空前显示数量并要求确认，清空后提供短时撤销。移除单条记录也应提供可感知的成功反馈。

### HCI-10：响应式布局对固定位置依赖较强（P2）

图谱首页的搜索栏、数据集、下载、控制菜单和详情浮层都是绝对定位；在窄屏断点下通过固定的 `top`、`bottom` 和 `max-height` 重新排列。页面还在多个后续 CSS 区块重复覆盖同名规则，最终效果依赖样式出现顺序，增加了不同尺寸下遮挡、滚动和关闭按钮不可见的风险。

证据：

- [frontend/src/styles.css:2624](/D:/yaorz/works/hci/project/frontend/src/styles.css:2624)-[2633](/D:/yaorz/works/hci/project/frontend/src/styles.css:2633)：多个首页控件统一采用 absolute positioning。
- [frontend/src/styles.css:5332](/D:/yaorz/works/hci/project/frontend/src/styles.css:5332)-[5364](/D:/yaorz/works/hci/project/frontend/src/styles.css:5364)：窄屏下将多个浮层移动到相同的底部区域。
- [frontend/src/styles.css:5367](/D:/yaorz/works/hci/project/frontend/src/styles.css:5367)-[5389](/D:/yaorz/works/hci/project/frontend/src/styles.css:5389)：移动端继续使用固定位置和高度策略。

建议：至少针对 320、375、768、1024、1440 px 做任务走查；将浮层改为有明确层级的布局容器，移动端用可滚动底部抽屉或普通文档流承载详情，保证关闭、返回和主要操作始终可见。统一 CSS 断点和覆盖规则，减少同一选择器的多次重定义。

## 任务流程评审

### 1. 发现数据

优点：首页直接把关系图作为主入口，数据集选择、搜索模式、下载入口和图谱控制都在首屏附近，视觉层级明确；搜索模式提供了条目、通路、同源和地图搜索几个任务方向。

问题：首次用户看不到“如何开始”的操作提示；“拖动地图到边缘以扩展”这一高价值行为只存在于错误提示文案中，没有在正常状态下被教会。首页品牌名与内页不一致，也削弱了产品整体感。

建议：在空闲状态增加一条简短的操作说明，例如“点击节点查看化合物；点击连线查看酶；拖动节点整理布局；拖到边缘加载更多”。同时提供一个可收起的图例，解释节点、连线、颜色和数量含义。

### 2. 检索和筛选

优点：搜索输入支持 Enter，结果类型和物种等筛选项有明确分组；搜索结果采用“列表 + 右侧详情”的工作台结构，适合连续核验。

问题：输入每变化一次就会触发延迟后的后端查询，没有“提交”与“正在查询范围”的清晰边界；结果项的详情状态可能与当前过滤结果脱节；搜索空状态缺少下一步动作。

建议：保留即时检索时，显示“正在搜索当前查询”；为无结果状态提供清除查询、清除筛选和打开帮助的操作；对后端搜索增加取消过期请求或请求序号，避免慢响应覆盖新查询。

### 3. 理解图关系

优点：点击 compound 会展示结构、ID、质量、分子式和 SMILES；点击 compound pair 会展开酶和反应列表；支持拖动布局、图谱扩展、路径高亮和控制节点/标签大小。

问题：交互方式的学习成本高，节点既可点击又可拖动，边缘扩展是隐藏机制；展开后酶卡的下载按钮只有图标，缺少明确的“已加入/移除”文字；关系标签在默认状态下部分隐藏，用户可能不知道连线代表什么。

建议：在图谱旁增加操作说明和“显示全部标签”选项；节点详情和酶列表中统一使用带文字的状态操作；给边增加方向、反应 ID 和证据类型的可见/可筛选表达。

### 4. 核验详情

优点：酶详情覆盖基因、序列链接、氨基酸序列、证据、反应和外部链接，信息密度适合专业用户；有 UniProt、DOI、PubMed、Rhea 等外链。

问题：详情页加载失败没有重试；序列复制无确认；反应和证据区域信息密度较高，缺少折叠或跳转目录，长页面回看成本较高。对于外部链接，用户可以看到外链图标，但没有统一说明会打开新标签页。

建议：增加详情页内目录或粘性锚点；反应、证据和链接支持折叠；复制操作增加状态反馈；外链统一使用“在新标签页打开”的可访问说明。

### 5. 加入队列并导出

优点：队列数量会在内页侧栏和顶部入口显示；队列按酶/通路分组，并提供打开记录、打开网络、移除和清空操作。

问题：当前队列主要是前端内存状态，刷新页面会丢失；预置条目没有来源说明；格式/列设置不生效；导出按钮没有预览、文件生成状态或成功反馈。

建议：先修复导出语义一致性，再决定是否需要 localStorage 或后端保存队列。导出流程应明确展示“选择 → 预览 → 生成 → 下载”的状态链路。

## 可访问性与视觉检查清单

- [ ] 图谱节点、边、浮层和关闭操作均可用键盘完成。
- [ ] 页面至少有一个清晰的主标题和可定位的主内容；首页图谱需要可读的列表替代视图。
- [ ] 所有图标按钮都有稳定的 `aria-label`，并能表达当前状态，例如 `aria-pressed`。
- [ ] 加载、成功、失败、复制成功、队列变更均通过 live region 或可见内联反馈传达。
- [ ] 搜索输入、筛选项、模式选择和下载格式选择均有明确 label，且当前选择有非颜色提示。
- [ ] 颜色不是区分 compound、enzyme、product 或状态的唯一线索；图例同时提供文字或形状编码。
- [ ] 在 200% 页面缩放、键盘 Tab、无鼠标和窄屏下走完一条完整任务。
- [ ] 检查深色工作区中低透明度文字与背景的实际对比度，尤其是侧栏小标签、摘要文字和浮层说明。

## 建议的修复顺序

### 第一阶段：保证结果可信

1. 重做下载页状态模型：格式、列、预览、生成和成功/失败状态。
2. 修复搜索结果队列状态，统一所有“加入/移除队列”控件的文案、图标和行为。
3. 明确实时数据、mock/fallback 数据和失败状态，增加重试。
4. 修复筛选/空结果时的 selection 同步。

### 第二阶段：让核心图谱可用

1. 增加正常状态下的图谱操作说明和列表替代视图。
2. 为节点、边和浮层补充键盘操作与 ARIA 语义。
3. 将图谱扩展、路径搜索和边展开的异步过程统一成状态反馈组件。
4. 为 BLAST/同源检索接入任务进度、取消和可解释结果排序。

### 第三阶段：提升连续工作效率

1. 统一 `Terpene Atlas`、`Search library`、`Download queue` 等产品术语。
2. 用 URL 保存页面、查询、筛选和选中实体状态。
3. 优化长详情页的目录、折叠和返回路径。
4. 清理重复 CSS 覆盖，建立响应式组件级断点测试。

## 已执行的验证

在 `frontend/` 目录执行：

```text
npm.cmd run build
```

结果：构建成功，TypeScript 检查和 Vite 生产构建均通过。当前 `package.json` 只提供构建和预览脚本，没有现成的自动化 UI 测试脚本；因此上述结论仍需要在真实后端、不同视口和键盘/屏幕阅读器环境中补充验证。

