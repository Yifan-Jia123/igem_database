import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUp,
  Dna,
  Download,
  ExternalLink,
  Link2,
  Loader2,
  Network,
  Plus,
  Route,
  Search,
  X,
} from 'lucide-react'
import {
  createEnzymeDownload,
  loadExpandedEdgeGroup,
  loadEnzymeDetail,
  loadGraphForEnzymes,
  loadHomeGraph,
  loadMetadataFilters,
  mapScopeSearch,
  runPathwaySearch as runPathwaySearchApi,
  searchApiEntries,
  suggestCompounds,
  type BlastHit,
  type BlastSession,
  type CompoundSuggestion,
  type EnzymeDetailData,
  type EnzymeSequenceLink,
  type HomeGraphCompound,
  type HomeGraphData,
  type HomeGraphEdge,
  type HomeGraphEdgeGroup,
  type HomeGraphEdgeGroupItem,
  type HomePathwayCard,
} from './api'
import { StructureSearchDrawer } from './components/StructureSearchDrawer'
import type { Entity, EntityKind, PathwayEnzymeChoice, PathwayQueueStep } from './types'

const HOME_EXPANSION_LIMIT = 36
const HOME_VIEWBOX_WIDTH = 100
const HOME_VIEWBOX_HEIGHT = 118
const HOME_LAYOUT_WIDTH = HOME_VIEWBOX_WIDTH * 2.8
const HOME_LAYOUT_HEIGHT = HOME_VIEWBOX_HEIGHT * 2.8
const HOME_LAYOUT_MIN_X = (HOME_VIEWBOX_WIDTH - HOME_LAYOUT_WIDTH) / 2
const HOME_LAYOUT_MIN_Y = (HOME_VIEWBOX_HEIGHT - HOME_LAYOUT_HEIGHT) / 2
const HOME_LAYOUT_MAX_X = HOME_LAYOUT_MIN_X + HOME_LAYOUT_WIDTH
const HOME_LAYOUT_MAX_Y = HOME_LAYOUT_MIN_Y + HOME_LAYOUT_HEIGHT
const HOME_LAYOUT_MARGIN = 12
const HOME_IMPORTANT_LABEL_COUNT = 10
const HOME_FORCE_ITERATIONS = 520
const HOME_FORCE_REPULSION = 246
const HOME_FORCE_LINK_DISTANCE = 72
const HOME_FORCE_LINK_STRENGTH = 0.0034
const HOME_FORCE_CENTERING = 0.00028
const HOME_FORCE_DAMPING = 0.68
const HOME_FORCE_COLLISION_DISTANCE = 21.6
const HOME_FORCE_COLLISION_STRENGTH = 0.5
const HOME_FINAL_COLLISION_DISTANCE = 21.6
const HOME_FINAL_COLLISION_ITERATIONS = 420
const HOME_GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5))
const homeSearchFilters = [
  { id: 'all', label: 'All' },
  { id: 'compound', label: 'Compounds' },
  { id: 'enzyme', label: 'Enzymes' },
  { id: 'reaction', label: 'Reactions' },
] as const
type HomeSearchFilter = (typeof homeSearchFilters)[number]['id']

type HomeActiveFilters = {
  species: string[]
  sourceTypes: string[]
}

const HOME_SOURCE_LABELS: Record<string, string> = {
  swiss_prot: 'Swiss-Prot',
  trembl: 'TrEMBL',
  ai_literature: 'AI (literature)',
  manual_literature: 'Manual (literature)',
}

const HOME_SOURCE_ORDER = ['swiss_prot', 'trembl', 'ai_literature', 'manual_literature']

function formatScopeEValue(value: number): string {
  return value === 0 ? '0' : value.toExponential(2)
}

type Point = { x: number; y: number }

type PairEntry = {
  key: string
  sourceId: string
  targetId: string
  label: string
  count: number
  edgeGroupId?: string | null
  edgeIds: string[]
  edges: HomeGraphEdge[]
}

type NodeCard = HomeGraphCompound & {
  degree: number
  x: number
  y: number
}

type ExpandedEdgeGroup = {
  key: string
  sourceId: string
  targetId: string
  enzymeId: string
  label: string
  directionMode: 'forward' | 'reverse' | 'bidirectional' | 'undirected'
  edges: HomeGraphEdge[]
  edgeIds: string[]
  reactionIds: string[]
  representative: HomeGraphEdge
}

type ForceLayoutLink = {
  sourceId: string
  targetId: string
  weight: number
}

type ExpansionDirection = 'left' | 'right' | 'top' | 'bottom'

type PanState = {
  pointerId: number
  startClientX: number
  startClientY: number
  originCamera: Point
  moved: boolean
}

type NodeDragState = {
  pointerId: number
  nodeId: string
  startClientX: number
  startClientY: number
  originPoint: Point
  moved: boolean
}

type PanelDragState = {
  pointerId: number
  startClientX: number
  startClientY: number
  originPoint: Point
}

type HomeSearchSuggestion = {
  id: string
  kind: EntityKind
  title: string
  subtitle: string
  nodeId?: string
  pairKey?: string
  edgeId?: string
  enzymeId?: string
  reactionId?: string
  entity?: Entity
}

type GraphSearchMatch =
  | { kind: 'node'; nodeId: string }
  | { kind: 'pair'; pair: PairEntry; edges: HomeGraphEdge[] }
  | { kind: 'none' }

/* ---------- Active-filter engine (organism + data source) ---------- */

function homeFiltersActive(filters: HomeActiveFilters) {
  return filters.species.length > 0 || filters.sourceTypes.length > 0
}

/** A single sub-edge (composite item or loaded edge) must pass every active dimension. */
function homeUnitPasses(unit: { organismName?: string | null; sourceType?: string | null }, filters: HomeActiveFilters) {
  if (filters.species.length > 0 && (!unit.organismName || !filters.species.includes(unit.organismName))) return false
  if (filters.sourceTypes.length > 0 && (!unit.sourceType || !filters.sourceTypes.includes(unit.sourceType))) return false
  return true
}

function homeEdgePasses(edge: HomeGraphEdge, filters: HomeActiveFilters) {
  return homeUnitPasses({ organismName: edge.card?.organismName ?? null, sourceType: edge.sourceType ?? null }, filters)
}

/** Sub-edges that survive the active filters for a collapsed composite pair (group items first, loaded edges fallback). */
function homePairPassingUnits(pair: PairEntry, groupItemMap: Map<string, HomeGraphEdgeGroupItem[]>, filters: HomeActiveFilters): (HomeGraphEdgeGroupItem | HomeGraphEdge)[] {
  const items = pair.edgeGroupId ? groupItemMap.get(pair.edgeGroupId) : undefined
  if (items && items.length > 0) return items.filter((item) => homeUnitPasses(item, filters))
  return pair.edges.filter((edge) => homeEdgePasses(edge, filters))
}

function homeSourceLabel(sourceType: string) {
  return HOME_SOURCE_LABELS[sourceType] || sourceType
}

function homeUnitLabel(unit: HomeGraphEdgeGroupItem | HomeGraphEdge) {
  if ('card' in unit && unit.card?.primaryName) return unit.card.primaryName
  return unit.label || null
}

/** Stable, compact on-edge annotation for a single sub-edge: UniProt accession when known. */
function homeUnitAccession(unit: HomeGraphEdgeGroupItem | HomeGraphEdge) {
  if ('card' in unit && unit.card) return unit.card.uniprotId || unit.label || null
  return unit.label || null
}

/** One active pathway-mode session: the returned union graph + its cards. */
type PathwaySessionData = {
  graph: HomeGraphData
  cards: HomePathwayCard[]
  total: number
  query: string
}

/** The composer payload a pathway run is launched with (tokens are resolved
 *  server-side, so they may be ids, bare ChEBI numbers or names). */
type PathwayComposerPayload = {
  startCompoundId: string
  endCompoundId: string
  viaCompoundIds: string[]
}

/** One oriented step (source→target compound pair) of the single route shown in
 *  the in-map detail sub-view. Composite steps carry ``groupId`` (their per-enzyme
 *  edges live behind loadExpandedEdgeGroup, not in the union graph's edges);
 *  single-edge steps carry ``edges`` (the backing edges). */
type PathwayDetailStep = {
  step: number
  sourceId: string
  targetId: string
  sourceName: string
  targetName: string
  groupId: string | null
  edges: HomeGraphEdge[]
}

export function CompoundGraphHome({
  onOpenSearch,
  onOpenDownloads,
  onOpenEnzyme,
  onOpenBlast,
  onOpenBlastTable,
  onToggleQueue,
  isQueued,
  queueCount,
  autoMapSearch,
  onAutoMapSearchConsumed,
  blastSession,
  autoBlastScope,
  onAutoBlastScopeConsumed,
  onResetHome,
}: {
  onOpenSearch: (query?: string) => void
  onOpenDownloads: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onOpenBlast: () => void
  onOpenBlastTable: () => void
  onToggleQueue: (entry: string | Entity) => void
  isQueued: (id: string) => boolean
  queueCount: number
  /** When the table-results page hands back to the map, run this query's scope search on mount. */
  autoMapSearch?: { query: string; nonce: number } | null
  onAutoMapSearchConsumed?: () => void
  /** Last completed BLAST run (for scoping the map to its hit enzymes). */
  blastSession?: BlastSession | null
  autoBlastScope?: { sessionId: number; nonce: number } | null
  onAutoBlastScopeConsumed?: () => void
  /** Starase Atlas brand → drop every active scope/search and head home. */
  onResetHome?: () => void
}) {
  const [graph, setGraph] = useState<HomeGraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [positions, setPositions] = useState<Record<string, Point>>({})
  const [camera, setCamera] = useState<Point>({ x: 0, y: 0 })
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null)
  const [expandedEdges, setExpandedEdges] = useState<HomeGraphEdge[]>([])
  const [expandedLoading, setExpandedLoading] = useState(false)
  const [mapExpanding, setMapExpanding] = useState(false)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<Set<string>>(new Set())
  const [highlightedEdgeIds, setHighlightedEdgeIds] = useState<Set<string>>(new Set())
  const [highlightedEdgeGroupIds, setHighlightedEdgeGroupIds] = useState<Set<string>>(new Set())
  const [activePathway, setActivePathway] = useState<HomePathwayCard | null>(null)
  /** Two search systems share the map surface. Enzyme mode is the original
   *  keyword/compound/BLAST search; pathway mode composes start → (via…) → end. */
  const [searchMode, setSearchMode] = useState<'enzyme' | 'pathway'>('enzyme')
  const [pathwaySession, setPathwaySession] = useState<PathwaySessionData | null>(null)
  const [selectedPathwayId, setSelectedPathwayId] = useState<string | null>(null)
  const [pathwaySearchLoading, setPathwaySearchLoading] = useState(false)
  /** Soft business errors surfaced into the composer (SAME_COMPOUND & friends). */
  const [pathwayError, setPathwayError] = useState<string | null>(null)
  /** The composer pill can be collapsed so the union-graph result is not blocked;
   *  a successful run hides it and shows a compact launcher instead. */
  const [composerOpen, setComposerOpen] = useState(true)
  /** In-map single-route detail sub-view. While non-null the map graph/positions/
   *  camera are swapped to exactly one returned route's chain; ``restore`` is the
   *  union-results view snapshot given back on "返回结果". Never Date.now()-keyed. */
  const [pathwayDetail, setPathwayDetail] = useState<{
    card: HomePathwayCard
    graph: HomeGraphData
    chain: string[]
    restore: { graph: HomeGraphData; positions: Record<string, Point>; camera: Point }
  } | null>(null)
  /** Right slide-in enzyme picker (per-step multi-select) opened by the detail
   *  bar's 下载 button. While open the map stays fully usable (no modal backdrop):
   *  this 0-based index into the route's step list (buildPathwayDetailSteps) is
   *  the picker's *current step*, shared with the map so that a step chip in the
   *  popup and a chain-edge click on the map both switch it (bidirectional).
   *  ``pickerGroupEdges`` caches each composite step's per-enzyme fan-out as it is
   *  opened (lazily, one network round-trip per group) and feeds both the popup's
   *  candidate list and the map's expanded-edge fan-out. */
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerStepIndex, setPickerStepIndex] = useState(0)
  const [pickerGroupEdges, setPickerGroupEdges] = useState<Record<string, HomeGraphEdge[]>>({})
  const [pickerGroupLoading, setPickerGroupLoading] = useState<string[]>([])
  const pickerGroupRequestedRef = useRef<Set<string>>(new Set())
  /** 连星 trace state. While ``traceChain`` is non-null the user is building a
   *  pathway by clicking map compounds one hop at a time. The chain always starts
   *  at the session's start compound; each next pick must be a compound the
   *  current last node feeds a directed (source → target) map pair into, and may
   *  not already be on the chain (no loops). Reaching the session end compound
   *  finishes the trace and returns a pathway card for the traced chain. */
  const [traceChain, setTraceChain] = useState<string[] | null>(null)
  /** One-line guidance shown in the trace bar (illegal pick, current hint...). */
  const [traceHint, setTraceHint] = useState<string | null>(null)
  const [searchFeedback, setSearchFeedback] = useState<string | null>(null)
  const [resultMode, setResultMode] = useState<'map' | 'table'>('map')
  const [scopeSearch, setScopeSearch] = useState<{
    query: string
    total: number
    shown: number
    kind: 'compound' | 'enzyme'
    anchorLabel?: string
    reactionCount?: number
  } | null>(null)
  const [noResult, setNoResult] = useState(false)
  const [noResultMessage, setNoResultMessage] = useState<string | null>(null)
  const [enzymeSearchLoading, setEnzymeSearchLoading] = useState(false)
  /** Active BLAST scope: the map shows the hit enzymes' neighbourhood subgraph. */
  const [blastScope, setBlastScope] = useState<{ sessionId: number; queryLength: number; searchedSubjects: number; threshold: number; hits: number } | null>(null)
  const [blastHitMap, setBlastHitMap] = useState<Map<string, BlastHit>>(new Map())
  /** Matched enzyme ids of the current keyword *enzyme* scope. Used to tell
   *  retrieved single edges (the searched enzymes) apart from background
   *  isoenzymes that surface when a composite edge is expanded. Null whenever no
   *  enzyme scope is active (browse / compound scope / BLAST uses blastHitMap). */
  const [enzymeScopeHitIds, setEnzymeScopeHitIds] = useState<Set<string> | null>(null)
  const [blastLoading, setBlastLoading] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [searchFocused, setSearchFocused] = useState(false)
  const [searchFilter, setSearchFilter] = useState<HomeSearchFilter>('all')
  const [librarySuggestions, setLibrarySuggestions] = useState<HomeSearchSuggestion[]>([])
  const [librarySearchLoading, setLibrarySearchLoading] = useState(false)
  const [selectedLibraryItem, setSelectedLibraryItem] = useState<Entity | null>(null)
  const [nodeSize, setNodeSize] = useState(1.8)
  const [edgeThickness, setEdgeThickness] = useState(1)
  const [labelFontScale, setLabelFontScale] = useState(1)
  const [structureOpen, setStructureOpen] = useState(false)
  const [activeFilters, setActiveFilters] = useState<HomeActiveFilters>({ species: [], sourceTypes: [] })
  const [speciesOptions, setSpeciesOptions] = useState<string[]>([])
  const [speciesMenuOpen, setSpeciesMenuOpen] = useState(false)
  const [speciesQuery, setSpeciesQuery] = useState('')
  const [activeNodeDragId, setActiveNodeDragId] = useState<string | null>(null)
  const [panelPosition, setPanelPosition] = useState<Point | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const panRef = useRef<PanState | null>(null)
  const nodeDragRef = useRef<NodeDragState | null>(null)
  const panelDragRef = useRef<PanelDragState | null>(null)
  const graphRef = useRef<HomeGraphData | null>(null)
  const positionsRef = useRef<Record<string, Point>>({})
  const cameraRef = useRef<Point>({ x: 0, y: 0 })
  const expansionKeysRef = useRef<Set<string>>(new Set())
  const expandingRef = useRef(false)
  const prevFiltersActiveRef = useRef(false)
  const browseSnapshotRef = useRef<{ graph: HomeGraphData; positions: Record<string, Point>; camera: Point } | null>(null)
  /** The pathway card that was selected when 连星 began, so Cancel can restore it. */
  const preTraceRef = useRef<{ card: HomePathwayCard | null; id: string | null }>({ card: null, id: null })
  /** Handle for the auto-dismissing 连星 hint message timer. */
  const traceHintTimerRef = useRef<number | null>(null)
  const autoSearchHandledRef = useRef<number | null>(null)
  const autoBlastHandledRef = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    loadHomeGraph()
      .then((payload) => {
        if (cancelled) return
        setGraph(payload)
        const layout = createHomeLayout(payload)
        setPositions(layout.positions)
        setCamera({ x: 0, y: 0 })
        setSelectedNodeId(null)
        setSelectedPairKey(null)
        setExpandedEdges([])
        setSelectedEdgeId(null)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load graph data')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    loadMetadataFilters()
      .then((payload) => {
        if (cancelled) return
        setSpeciesOptions((payload.organisms || []).slice())
      })
      .catch(() => {
        if (!cancelled) setSpeciesOptions([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    graphRef.current = graph
  }, [graph])

  useEffect(() => {
    positionsRef.current = positions
  }, [positions])

  useEffect(() => {
    cameraRef.current = camera
  }, [camera])

  useEffect(() => {
    // The trace-hint message auto-dismisses on a timer; clear it on unmount so
    // it never touches unmounted state (React warns otherwise).
    return () => {
      if (traceHintTimerRef.current !== null) window.clearTimeout(traceHintTimerRef.current)
    }
  }, [])

  useEffect(() => {
    const movePanel = (event: PointerEvent) => {
      const dragState = panelDragRef.current
      if (!dragState || dragState.pointerId !== event.pointerId) return
      const nextPoint = clampPanelPosition({
        x: dragState.originPoint.x + event.clientX - dragState.startClientX,
        y: dragState.originPoint.y + event.clientY - dragState.startClientY,
      })
      setPanelPosition(nextPoint)
    }
    const finishPanel = (event: PointerEvent) => {
      const dragState = panelDragRef.current
      if (!dragState || dragState.pointerId !== event.pointerId) return
      panelDragRef.current = null
    }
    window.addEventListener('pointermove', movePanel)
    window.addEventListener('pointerup', finishPanel)
    window.addEventListener('pointercancel', finishPanel)
    return () => {
      window.removeEventListener('pointermove', movePanel)
      window.removeEventListener('pointerup', finishPanel)
      window.removeEventListener('pointercancel', finishPanel)
    }
  }, [])

  const viewModel = useMemo(() => createHomeViewModel(graph, positions, selectedPairKey, expandedEdges), [graph, positions, selectedPairKey, expandedEdges])
  const selectedPair = viewModel.pairs.find((pair) => pair.key === selectedPairKey) ?? null
  const selectedNode = viewModel.nodes.find((node) => node.compoundId === selectedNodeId) ?? null
  const groupItemMap = useMemo(() => {
    const map = new Map<string, HomeGraphEdgeGroupItem[]>()
    graph?.edgeGroups.forEach((group) => {
      if (group.items && group.items.length > 0) map.set(group.edgeGroupId, group.items)
    })
    return map
  }, [graph])
  // A pathway is a chain of directed compound pairs and every map curve is one
  // (source → target) pair, so pathway emphasis is matched on the pair key —
  // never on raw enzyme-edge ids: one enzyme row can back several pairs (bi-bi
  // reactions, reversible rows), and per-id matching would bleed the highlight
  // onto sibling pairs of the same enzyme (the "wrong edges lighting up" bug).
  const activePathwayStepKeys = useMemo(() => {
    if (!activePathway) return null
    const chain = activePathway.compoundIds
    if (!chain || chain.length < 2) return null
    const keys = new Set<string>()
    for (let index = 0; index < chain.length - 1; index += 1) {
      keys.add(pairKey(chain[index], chain[index + 1]))
    }
    return keys
  }, [activePathway])
  const anyFilterActive = homeFiltersActive(activeFilters)
  // A pathway session pins the map to its union graph: browsing must not expand
  // neighbourhoods into it (drag-to-edge is gated on scopeActive at :522).
  const scopeActive = Boolean(scopeSearch || blastScope || pathwaySession)
  /** True while the in-map single-route detail sub-view is open. Gates every
   *  pathway-mode floating panel (composer/launcher/连星/results list) off. */
  const detailOpen = pathwayDetail !== null
  /** 1..stepCount step list of the open detail route. Memoised once per detail so
   *  the picker's step bar, the picker step-state effects and the map→step edge
   *  click mapping all share one stable array (buildPathwayDetailSteps is pure). */
  const detailSteps = useMemo(
    () => (pathwayDetail ? buildPathwayDetailSteps(pathwayDetail.card, pathwayDetail.graph) : []),
    [pathwayDetail],
  )
  const speciesSelectOptions = useMemo(() => {
    const present = new Set<string>()
    speciesOptions.forEach((species) => present.add(species))
    groupItemMap.forEach((items) => items.forEach((item) => { if (item.organismName) present.add(item.organismName) }))
    return [...present].sort((a, b) => a.localeCompare(b))
  }, [speciesOptions, groupItemMap])
  const pairFilterMeta = useMemo(() => {
    const meta = new Map<string, { visible: boolean; passing: number; singleLabel: string | null }>()
    if (anyFilterActive) {
      viewModel.pairs.forEach((pair) => {
        const passing = homePairPassingUnits(pair, groupItemMap, activeFilters)
        const first = passing[0]
        meta.set(pair.key, {
          visible: passing.length > 0,
          passing: passing.length,
          singleLabel: passing.length === 1 && first ? homeUnitLabel(first) : null,
        })
      })
    }
    return meta
  }, [anyFilterActive, viewModel.pairs, groupItemMap, activeFilters])
  const pairEdges = selectedPairKey ? (expandedEdges.length > 0 ? expandedEdges : selectedPair?.edges ?? []).filter((edge) => (anyFilterActive ? homeEdgePasses(edge, activeFilters) : true)) : []
  const expandedEdgeGroups = useMemo(
    () => groupExpandedEdgesByEnzyme(pairEdges, selectedPair?.sourceId, selectedPair?.targetId),
    [pairEdges, selectedPair?.sourceId, selectedPair?.targetId],
  )
  // Enzyme ids that count as "retrieved by the active search". BLAST scopes use
  // the hit map; keyword enzyme scopes use the ids that matched. Compound scopes
  // and the plain browse map have no retrieved set — expanded single edges keep
  // their ordinary look there.
  const scopeHitSet = useMemo(() => {
    if (blastScope && blastHitMap.size > 0) return new Set(blastHitMap.keys())
    if (scopeSearch?.kind === 'enzyme') return enzymeScopeHitIds
    return null
  }, [blastScope, blastHitMap, enzymeScopeHitIds, scopeSearch])
  const selectedExpandedGroup = expandedEdgeGroups.find((group) => group.edgeIds.includes(selectedEdgeId || '')) || expandedEdgeGroups[0] || null
  const selectedPairTotal = selectedPair ? Math.max(selectedPair.count, selectedPair.edges.length) : 0
  const visibleEdgeCount = viewModel.pairs.reduce((sum, pair) => sum + Math.max(pair.count, pair.edges.length || 0), 0)
  const compoundName = (compoundId: string) => viewModel.nodes.find((node) => node.compoundId === compoundId)?.name || compoundId
  // ---- 连星 support data -------------------------------------------------
  // Every server-returned card begins at the session start compound and ends at
  // the session end compound, so those anchor the trace. Legal hops come from the
  // directed (source → target) pair graph actually drawn, which is exactly the
  // graph a traced chain has to light up; organism/source filters hide edges
  // only at render time, so a filtered-out pair is also not a legal hop.
  const traceStartId = pathwaySession?.cards[0]?.compoundIds[0] ?? null
  const traceEndId = (() => {
    const ids = pathwaySession?.cards[0]?.compoundIds
    return ids && ids.length > 0 ? ids[ids.length - 1] : null
  })()
  const traceAdjacency = useMemo(() => {
    const map = new Map<string, Set<string>>()
    viewModel.pairs.forEach((pair) => {
      if (anyFilterActive) {
        const meta = pairFilterMeta.get(pair.key)
        if (!meta || !meta.visible) return
      }
      const targets = map.get(pair.sourceId)
      if (targets) targets.add(pair.targetId)
      else map.set(pair.sourceId, new Set([pair.targetId]))
    })
    return map
  }, [viewModel.pairs, anyFilterActive, pairFilterMeta])
  const traceCurrentId = traceChain ? traceChain[traceChain.length - 1] : null
  /** Compounds the current last node may legally step into next: its visible
   *  out-neighbours minus anything already on the trace (the no-loop rule). */
  const traceNextIds = useMemo(() => {
    if (!traceChain || traceChain.length === 0) return null
    const onChain = new Set(traceChain)
    const next = new Set<string>()
    const outgoing = traceAdjacency.get(traceChain[traceChain.length - 1])
    if (outgoing) outgoing.forEach((id) => { if (!onChain.has(id)) next.add(id) })
    return next
  }, [traceChain, traceAdjacency])
  /** Build a synthetic pathway card for a chain the user traced by clicking, so
   *  the traced route can be highlighted and returned exactly like a server card.
   *  Steps resolve against the session union graph's real pair/edge ids. */
  const makeTracedCard = (chain: string[]): HomePathwayCard => {
    const steps = Math.max(chain.length - 1, 0)
    const edgeIds: string[] = []
    const edgeGroupIds: string[] = []
    const segments: { sourceCompoundId: string; targetCompoundId: string; edgeId: string | null; edgeGroupId: string | null }[] = []
    for (let index = 0; index < steps; index += 1) {
      const from = chain[index]
      const to = chain[index + 1]
      const pair = viewModel.pairs.find((p) => p.sourceId === from && p.targetId === to)
      const singleId = pair && !pair.edgeGroupId ? pair.edges[0]?.edgeId ?? pair.edgeIds[0] ?? null : null
      const groupId = pair?.edgeGroupId ?? null
      if (singleId) edgeIds.push(singleId)
      if (groupId) edgeGroupIds.push(groupId)
      segments.push({ sourceCompoundId: from, targetCompoundId: to, edgeId: singleId, edgeGroupId: groupId })
    }
    return {
      pathwayId: `TRACE_${chain.join('_')}`,
      summary: chain.map((id) => compoundName(id)).join(' → '),
      compoundIds: chain.slice(),
      edgeIds,
      edgeGroupIds,
      segments,
      stepCount: steps,
      score: null,
      graph: null,
    }
  }
  const sameCompoundChain = (a: string[], b: string[]) => a.length === b.length && a.every((id, index) => id === b[index])
  const importantLabelIds = useMemo(() => pickImportantHomeLabelIds(viewModel.nodes), [viewModel.nodes])
  const selectedEdge = selectedExpandedGroup?.representative || pairEdges.find((edge) => edge.edgeId === selectedEdgeId) || pairEdges[0] || null
  const trimmedSearchValue = searchValue.trim()
  const localSearchSuggestions = useMemo(
    () => buildHomeSearchSuggestions(trimmedSearchValue, graph, viewModel.pairs, searchFilter),
    [trimmedSearchValue, graph, viewModel.pairs, searchFilter],
  )
  const visibleSearchSuggestions = useMemo(() => {
    const localKeys = new Set(localSearchSuggestions.map((item) => item.id))
    const remoteItems = librarySuggestions.filter((item) => {
      if (searchFilter !== 'all' && item.kind !== searchFilter) return false
      return !localKeys.has(item.id)
    })
    return [...localSearchSuggestions, ...remoteItems].slice(0, 10)
  }, [localSearchSuggestions, librarySuggestions, searchFilter])
  const showSearchSuggestions = searchFocused && trimmedSearchValue.length > 0
  const panelStyle: CSSProperties | undefined = panelPosition
    ? { left: panelPosition.x, top: panelPosition.y, right: 'auto', bottom: 'auto' }
    : undefined

  useEffect(() => {
    if ((searchFilter !== 'all' && searchFilter !== 'enzyme') || trimmedSearchValue.length < 2) {
      setLibrarySuggestions([])
      setLibrarySearchLoading(false)
      return
    }

    let cancelled = false
    setLibrarySearchLoading(true)
    const timer = window.setTimeout(() => {
      searchApiEntries({ q: trimmedSearchValue, pageSize: 8 })
        .then((items) => {
          if (cancelled) return
          setLibrarySuggestions(
            items
              .filter((item) => item.kind === 'enzyme')
              .map((item) => ({
                id: `library:enzyme:${item.id}`,
                kind: 'enzyme' as const,
                title: item.name,
                subtitle: [item.subtitle, item.species].filter(Boolean).join(' · ') || item.id,
                enzymeId: item.id,
                entity: item,
              })),
          )
        })
        .catch(() => {
          if (!cancelled) setLibrarySuggestions([])
        })
        .finally(() => {
          if (!cancelled) setLibrarySearchLoading(false)
        })
    }, 180)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [searchFilter, trimmedSearchValue])

  const focusCameraOnPoint = (point: Point, target: Point = { x: 58, y: 56 }) => {
    const nextCamera = { x: target.x - point.x, y: target.y - point.y }
    setCamera(nextCamera)
    cameraRef.current = nextCamera
  }

  const focusCameraOnNode = (compoundId: string, target: Point = { x: 38, y: 54 }) => {
    const point = positionsRef.current[compoundId]
    if (point) focusCameraOnPoint(point, target)
  }

  const focusCameraOnPair = (pair: PairEntry, target: Point = { x: 38, y: 54 }) => {
    const source = positionsRef.current[pair.sourceId]
    const targetNode = positionsRef.current[pair.targetId]
    if (!source || !targetNode) return
    focusCameraOnPoint({ x: (source.x + targetNode.x) / 2, y: (source.y + targetNode.y) / 2 }, target)
  }

  const expandFromNodeAtEdge = async (nodeId: string, direction: ExpansionDirection) => {
    if (expandingRef.current) return
    if (scopeActive) return
    const currentGraph = graphRef.current
    if (!currentGraph) return
    const expansionKey = `${nodeId}:${direction}`
    if (expansionKeysRef.current.has(expansionKey)) return
    expandingRef.current = true
    expansionKeysRef.current.add(expansionKey)
    setMapExpanding(true)
    try {
      const payload = await loadHomeGraph({ centerCompoundId: nodeId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT })
      const merged = mergeHomeGraph(graphRef.current, payload)
      const previousPositionCount = Object.keys(positionsRef.current).length
      const nextPositions = addExpansionPositions(positionsRef.current, payload, nodeId, direction)
      const addedCount = Object.keys(nextPositions).length - previousPositionCount
      graphRef.current = merged
      positionsRef.current = nextPositions
      setGraph(merged)
      setPositions(nextPositions)
      setHighlightedNodeIds(new Set([nodeId]))
      setSearchFeedback(addedCount > 0 ? `Expanded around ${compoundName(nodeId)} (+${addedCount})` : `No new compounds beyond ${compoundName(nodeId)}`)
    } catch (err) {
      setSearchFeedback(err instanceof Error ? err.message : 'Unable to expand this map area.')
    } finally {
      expandingRef.current = false
      setMapExpanding(false)
    }
  }

  const maybeExpandNodeAtViewportEdge = (nodeId: string) => {
    const point = positionsRef.current[nodeId]
    if (!point) return
    const direction = getNodeExpansionDirection(point, cameraRef.current)
    if (direction) void expandFromNodeAtEdge(nodeId, direction)
  }

  const handleMapPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return
    panRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originCamera: cameraRef.current,
      moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handleMapPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (nodeDragRef.current) {
      updateNodeDrag(event.pointerId, event.clientX, event.clientY)
      return
    }
    const panState = panRef.current
    const svg = svgRef.current
    if (!panState || panState.pointerId !== event.pointerId || !svg) return
    const rect = svg.getBoundingClientRect()
    const deltaX = ((event.clientX - panState.startClientX) / Math.max(rect.width, 1)) * HOME_VIEWBOX_WIDTH
    const deltaY = ((event.clientY - panState.startClientY) / Math.max(rect.height, 1)) * HOME_VIEWBOX_HEIGHT
    if (Math.abs(deltaX) > 0.8 || Math.abs(deltaY) > 0.8) panState.moved = true
    const nextCamera = { x: panState.originCamera.x + deltaX, y: panState.originCamera.y + deltaY }
    cameraRef.current = nextCamera
    setCamera(nextCamera)
  }

  const finishMapPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (nodeDragRef.current) {
      finishNodeDragByPointer(event.pointerId)
      return
    }
    const panState = panRef.current
    if (!panState || panState.pointerId !== event.pointerId) return
    panRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const handleNodePointerDown = (event: ReactPointerEvent<SVGCircleElement>, node: NodeCard, point: Point) => {
    if (event.button !== 0) return
    event.stopPropagation()
    // While tracing, taps route through handleTraceTap which owns what stays
    // selected; do not drop the live partial-chain highlight on pointer-down.
    if (!traceChain) {
      setSelectedNodeId(null)
      setSelectedPairKey(null)
      setExpandedEdges([])
      setSelectedEdgeId(null)
      setActivePathway(null)
      setSelectedLibraryItem(null)
    }
    nodeDragRef.current = {
      pointerId: event.pointerId,
      nodeId: node.compoundId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originPoint: point,
      moved: false,
    }
    setActiveNodeDragId(node.compoundId)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const updateNodeDrag = (pointerId: number, clientX: number, clientY: number) => {
    const dragState = nodeDragRef.current
    const svg = svgRef.current
    if (!dragState || dragState.pointerId !== pointerId || !svg) return false
    const delta = svgPointerDelta(svg, dragState.startClientX, dragState.startClientY, clientX, clientY)
    if (Math.abs(delta.x) > 0.35 || Math.abs(delta.y) > 0.35) dragState.moved = true
    const nextPoint = {
      x: dragState.originPoint.x + delta.x,
      y: dragState.originPoint.y + delta.y,
    }
    const nextPositions = {
      ...positionsRef.current,
      [dragState.nodeId]: nextPoint,
    }
    positionsRef.current = nextPositions
    setPositions(nextPositions)
    const direction = getNodeExpansionDirection(nextPoint, cameraRef.current)
    if (dragState.moved && direction) void expandFromNodeAtEdge(dragState.nodeId, direction)
    return true
  }

  const finishNodeDragByPointer = (pointerId: number) => {
    const dragState = nodeDragRef.current
    if (!dragState || dragState.pointerId !== pointerId) return false
    nodeDragRef.current = null
    setActiveNodeDragId(null)
    if (dragState.moved) {
      maybeExpandNodeAtViewportEdge(dragState.nodeId)
      return true
    }
    handleNodeSelect(dragState.nodeId)
    return true
  }

  const handleNodePointerMove = (event: ReactPointerEvent<SVGCircleElement>) => {
    if (!nodeDragRef.current) return
    event.stopPropagation()
    updateNodeDrag(event.pointerId, event.clientX, event.clientY)
  }

  const finishNodeDrag = (event: ReactPointerEvent<SVGCircleElement>) => {
    if (!nodeDragRef.current) return
    event.stopPropagation()
    finishNodeDragByPointer(event.pointerId)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const handlePanelPointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return
    const target = event.target instanceof Element ? event.target : null
    if (target?.closest('button, a, input, textarea, select')) return
    const panel = event.currentTarget.closest('.map-draggable-panel')
    if (!(panel instanceof HTMLElement)) return
    const rect = panel.getBoundingClientRect()
    const originPoint = panelPosition || { x: rect.left, y: rect.top }
    panelDragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originPoint,
    }
    setPanelPosition(originPoint)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePanelPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const dragState = panelDragRef.current
    if (!dragState || dragState.pointerId !== event.pointerId) return
    const nextPoint = clampPanelPosition({
      x: dragState.originPoint.x + event.clientX - dragState.startClientX,
      y: dragState.originPoint.y + event.clientY - dragState.startClientY,
    })
    setPanelPosition(nextPoint)
  }

  const finishPanelDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const dragState = panelDragRef.current
    if (!dragState || dragState.pointerId !== event.pointerId) return
    panelDragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  useEffect(() => {
    if (selectedEdgeId && pairEdges.some((edge) => edge.edgeId === selectedEdgeId)) return
    setSelectedEdgeId(pairEdges[0]?.edgeId ?? null)
  }, [pairEdges, selectedEdgeId])

  const selectPair = async (pair: PairEntry, targetEdge?: { edgeId?: string; enzymeId?: string; reactionId?: string }) => {
    setSelectedPairKey(pair.key)
    setSelectedNodeId(null)
    setActivePathway(null)
    setSelectedLibraryItem(null)
    setHighlightedNodeIds(new Set([pair.sourceId, pair.targetId]))
    setHighlightedEdgeGroupIds(new Set([pair.edgeGroupId || pair.key]))
    setSearchFeedback(null)
    focusCameraOnPair(pair)
    let nextEdges: HomeGraphEdge[]
    if (pair.edges.length > 0 && pair.edges.length === pair.count) {
      nextEdges = pair.edges
    } else if (pair.edgeGroupId) {
      setExpandedLoading(true)
      try {
        const edges = await loadExpandedEdgeGroup(pair.edgeGroupId)
        nextEdges = edges.length > 0 ? edges : pair.edges
      } finally {
        setExpandedLoading(false)
      }
    } else {
      nextEdges = pair.edges
    }
    commitPairEdges(nextEdges, targetEdge)
  }

  const commitPairEdges = (nextEdges: HomeGraphEdge[], targetEdge?: { edgeId?: string; enzymeId?: string; reactionId?: string }) => {
    const filteredEdges = anyFilterActive ? nextEdges.filter((edge) => homeEdgePasses(edge, activeFilters)) : nextEdges
    if (filteredEdges.length === 0) {
      clearPairSelection()
      return
    }
    const selected = pickTargetEdge(filteredEdges, targetEdge) || filteredEdges[0] || null
    setExpandedEdges(filteredEdges)
    setSelectedEdgeId(selected?.edgeId ?? null)
    setHighlightedEdgeIds(new Set(selected ? [selected.edgeId] : filteredEdges.map((edge) => edge.edgeId)))
  }

  const handlePairClick = async (pair: PairEntry) => {
    // Opening the enzyme stack would displace the results list the trace feeds;
    // leave the trace alone when an edge is clicked mid-trace.
    if (traceChain) {
      showTraceHint('Finish or cancel the trace before inspecting an edge.')
      return
    }
    // While the picker is open the map stays fully operable and NEVER closes it.
    // Clicking a chain edge instead retargets the popup to that step and fans its
    // composite edge out — the "点边" half of the bidirectional linkage.
    if (pickerOpen && pathwayDetail) {
      const index = detailSteps.findIndex(
        (step) => step.sourceId === pair.sourceId && step.targetId === pair.targetId,
      )
      if (index >= 0) {
        setPickerActiveStep(index)
        return
      }
    }
    await selectPair(pair)
  }

  const clearPairSelection = () => {
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setSelectedNodeId(null)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setActivePathway(null)
    setSelectedLibraryItem(null)
    setSearchFeedback(null)
  }

  const toggleSpeciesFilter = (species: string) => {
    setActiveFilters((prev) => ({
      ...prev,
      species: prev.species.includes(species) ? prev.species.filter((item) => item !== species) : [...prev.species, species],
    }))
  }

  const toggleSourceFilter = (sourceType: string) => {
    setActiveFilters((prev) => ({
      ...prev,
      sourceTypes: prev.sourceTypes.includes(sourceType) ? prev.sourceTypes.filter((item) => item !== sourceType) : [...prev.sourceTypes, sourceType],
    }))
  }

  const resetActiveFilters = () => {
    setActiveFilters({ species: [], sourceTypes: [] })
    setSpeciesQuery('')
    setSpeciesMenuOpen(false)
  }

  // If the current filters hide every expanded sub-edge of the open selection, drop back to browse state.
  useEffect(() => {
    if (!selectedPairKey || !anyFilterActive || expandedEdges.length === 0) return
    const passing = expandedEdges.filter((edge) => homeEdgePasses(edge, activeFilters))
    if (passing.length === 0) {
      clearPairSelection()
    } else if (passing.length !== expandedEdges.length) {
      setExpandedEdges(passing)
      setSelectedEdgeId((current) => (current && passing.some((edge) => edge.edgeId === current) ? current : (passing[0]?.edgeId ?? null)))
    }
  }, [selectedPairKey, anyFilterActive, expandedEdges, activeFilters])

  // Clearing the filters while a pair is expanded would keep a stale filtered subset;
  // collapse so the next click reloads the full edge set.
  useEffect(() => {
    const wasActive = prevFiltersActiveRef.current
    prevFiltersActiveRef.current = anyFilterActive
    if (wasActive && !anyFilterActive && selectedPairKey && expandedEdges.length > 0) {
      clearPairSelection()
    }
  }, [anyFilterActive, selectedPairKey, expandedEdges])

  // ---- 连星 trace interactions ------------------------------------------
  const clearTraceHintTimer = () => {
    if (traceHintTimerRef.current !== null) {
      window.clearTimeout(traceHintTimerRef.current)
      traceHintTimerRef.current = null
    }
  }
  const showTraceHint = (message: string) => {
    setTraceHint(message)
    clearTraceHintTimer()
    traceHintTimerRef.current = window.setTimeout(() => setTraceHint(null), 3600)
  }
  const stopTrace = () => {
    setTraceChain(null)
    setTraceHint(null)
    clearTraceHintTimer()
  }
  /** Enter 连星: anchor on the session start compound and let the user click its
   *  way through direct neighbours. The previous selection is remembered so the
   *  Cancel button can hand the map back to it. */
  const beginTrace = () => {
    if (!pathwaySession || traceChain || !traceStartId) return
    preTraceRef.current = { card: activePathway, id: selectedPathwayId }
    setComposerOpen(false)
    setSelectedPairKey(null)
    setSelectedNodeId(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setSelectedLibraryItem(null)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setTraceHint(null)
    setTraceChain([traceStartId])
    // Single-node chain: the start ring anchors where the trace begins.
    setActivePathway(makeTracedCard([traceStartId]))
  }
  const undoTrace = () => {
    if (!traceChain || traceChain.length <= 1) return
    const next = traceChain.slice(0, -1)
    setTraceChain(next)
    setActivePathway(makeTracedCard(next))
    setTraceHint(null)
  }
  /** Leave the trace and restore whatever pathway card was selected when it began. */
  const cancelTrace = () => {
    const prior = preTraceRef.current
    stopTrace()
    preTraceRef.current = { card: null, id: null }
    if (prior.card) {
      selectPathwayCard(prior.card)
    } else {
      setActivePathway(null)
      setSelectedPathwayId(null)
      setHighlightedNodeIds(new Set())
      setHighlightedEdgeIds(new Set())
      setHighlightedEdgeGroupIds(new Set())
    }
  }
  /** The trace reached its endpoint: surface the traced chain as a result card.
   *  If it coincides with a server-returned card, that row is selected instead
   *  of inserting a duplicate. */
  const finishTrace = (chain: string[]) => {
    const card = makeTracedCard(chain)
    const duplicate = pathwaySession?.cards.find((existing) => sameCompoundChain(existing.compoundIds, chain))
    stopTrace()
    if (duplicate) {
      selectPathwayCard(duplicate)
      return
    }
    setPathwaySession((prev) => (prev ? { ...prev, cards: [card, ...prev.cards] } : prev))
    selectPathwayCard(card)
  }
  /** One node click while tracing. Rules: the pick must be a direct visible
   *  out-neighbour of the current last node, and must not already be on the
   *  chain (no loops). Landing on the session end compound finishes the trace. */
  const handleTraceTap = (compoundId: string) => {
    const chain = traceChain
    if (!chain || chain.length === 0) return
    if (chain.includes(compoundId)) {
      showTraceHint('This compound is already on the trace — you cannot loop back to it.')
      return
    }
    if (!traceNextIds?.has(compoundId)) {
      showTraceHint('Pick one of the ringed compounds directly connected to the current one.')
      return
    }
    const next = [...chain, compoundId]
    if (compoundId === traceEndId) {
      finishTrace(next)
      return
    }
    setTraceChain(next)
    setActivePathway(makeTracedCard(next))
    setTraceHint(null)
  }

  const handleNodeSelect = (compoundId: string) => {
    if (traceChain) {
      handleTraceTap(compoundId)
      return
    }
    setSelectedNodeId(compoundId)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setActivePathway(null)
    setSelectedLibraryItem(null)
    setHighlightedNodeIds(new Set([compoundId]))
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setSearchFeedback(null)
    focusCameraOnNode(compoundId)
  }

  const resetLayout = () => {
    if (!graph) return
    const layout = createHomeLayout(graph)
    setPositions(layout.positions)
    setCamera({ x: 0, y: 0 })
    setSelectedNodeId(null)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setActivePathway(null)
    setSelectedLibraryItem(null)
    setSearchFeedback(null)
  }

  const handleSearchSuggestionSelect = async (suggestion: HomeSearchSuggestion) => {
    setSearchValue(suggestion.title)
    setSearchFocused(false)
    setSelectedLibraryItem(null)
    if (suggestion.kind === 'compound' && suggestion.nodeId) {
      handleNodeSelect(suggestion.nodeId)
      setSearchFeedback(`Focused compound: ${suggestion.title}`)
      return
    }

    if ((suggestion.kind === 'reaction' || suggestion.kind === 'enzyme') && suggestion.pairKey) {
      const pair = viewModel.pairs.find((item) => item.key === suggestion.pairKey)
      if (pair) {
        await selectPair(pair, { edgeId: suggestion.edgeId, enzymeId: suggestion.enzymeId, reactionId: suggestion.reactionId })
        setSearchFeedback(`Focused ${suggestion.kind}: ${suggestion.title}`)
        return
      }
    }

    if (suggestion.kind === 'enzyme' && suggestion.enzymeId) {
      await focusLibraryEnzymeSuggestion(suggestion)
      return
    }

    setSearchFeedback('This result is not connected to the loaded map yet.')
  }

  const focusLibraryEnzymeSuggestion = async (suggestion: HomeSearchSuggestion) => {
    if (suggestion.entity) setSelectedLibraryItem(suggestion.entity)
    if (!suggestion.enzymeId) return
    setMapExpanding(true)
    try {
      const detail = await loadEnzymeDetail(suggestion.enzymeId)
      const reaction = detail.reactions.find((item) => item.substrates.length > 0 && item.products.length > 0)
      if (!reaction) {
        setSearchFeedback(`Found enzyme: ${detail.primaryName}`)
        return
      }
      const sourceId = reaction.substrates[0]?.compoundId
      const targetId = reaction.products[0]?.compoundId
      if (!sourceId || !targetId) {
        setSearchFeedback(`Found enzyme: ${detail.primaryName}`)
        return
      }

      const payload = await loadHomeGraph({ centerCompoundId: sourceId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT })
      const merged = mergeHomeGraph(graphRef.current, payload)
      const seedPoint = positionsRef.current[sourceId] || {
        x: 42 - cameraRef.current.x,
        y: 54 - cameraRef.current.y,
      }
      const seededPositions = {
        ...positionsRef.current,
        [sourceId]: seedPoint,
      }
      const nextPositions = addExpansionPositions(seededPositions, payload, sourceId, 'right')
      graphRef.current = merged
      positionsRef.current = nextPositions
      setGraph(merged)
      setPositions(nextPositions)

      const pair = findPairForEndpoints(merged, sourceId, targetId)
      if (pair) {
        await selectPair(pair, { enzymeId: suggestion.enzymeId, reactionId: reaction.reactionId })
        setSearchFeedback(`Focused enzyme: ${detail.primaryName}`)
      } else {
        setSelectedNodeId(sourceId)
        setSelectedPairKey(null)
        setExpandedEdges([])
        setSelectedEdgeId(null)
        setHighlightedNodeIds(new Set([sourceId, targetId]))
        setHighlightedEdgeIds(new Set())
        setHighlightedEdgeGroupIds(new Set())
        focusCameraOnNode(sourceId)
        setSearchFeedback(`Loaded neighborhood for ${detail.primaryName}`)
      }
    } catch (err) {
      setSearchFeedback(err instanceof Error ? err.message : 'Unable to locate this enzyme on the map.')
    } finally {
      setMapExpanding(false)
    }
  }

  /** Drop the pathway session and, when it was showing its union graph on the
   *  map, hand the browse graph back. Keeps ``searchMode`` untouched so the
   *  caller decides which system owns the surface next. */
  const clearPathwayResults = () => {
    if (pathwaySession) {
      restoreBrowseGraph()
      browseSnapshotRef.current = null
    }
    // A brand click / mode switch / fresh run exits the in-map detail sub-view too,
    // so a stale single-chain graph can never linger after the session is dropped.
    setPathwayDetail(null)
    setPickerOpen(false)
    stopTrace()
    preTraceRef.current = { card: null, id: null }
    setPathwaySession(null)
    setSelectedPathwayId(null)
    setActivePathway(null)
    setPathwayError(null)
    setNoResult(false)
    setNoResultMessage(null)
    setPathwaySearchLoading(false)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setComposerOpen(true)
  }

  /** Flip the map's top search bar between the enzyme and the pathway system. */
  const switchSearchMode = (mode: 'enzyme' | 'pathway') => {
    if (mode === searchMode) return
    setSearchMode(mode)
    setSearchFocused(false)
    if (mode === 'enzyme') clearPathwayResults()
  }

  const restoreBrowseGraph = () => {
    const snapshot = browseSnapshotRef.current
    if (snapshot) {
      graphRef.current = snapshot.graph
      positionsRef.current = snapshot.positions
      setGraph(snapshot.graph)
      setPositions(snapshot.positions)
      setCamera(snapshot.camera)
      cameraRef.current = snapshot.camera
    }
  }

  const clearSearchScope = () => {
    clearPathwayResults()
    restoreBrowseGraph()
    browseSnapshotRef.current = null
    setScopeSearch(null)
    setBlastScope(null)
    setBlastHitMap(new Map())
    setEnzymeScopeHitIds(null)
    setNoResult(false)
    setNoResultMessage(null)
    setEnzymeSearchLoading(false)
    setSearchValue('')
    setSearchFocused(false)
    clearPairSelection()
  }

  /** Brand click: drop any scope/search running on this map, then hand the
   *  parent a chance to reset the app-wide search state and land on home. */
  const handleBrandHome = () => {
    switchSearchMode('enzyme')
    clearSearchScope()
    onResetHome?.()
  }

  const runMapEnzymeSearch = async (query: string) => {
    // An enzyme keyword run owns the surface: leave pathway mode, drop any
    // pathway session, then fall back to the browse graph before showing the new result.
    setSearchMode('enzyme')
    clearPathwayResults()
    restoreBrowseGraph()
    const currentGraph = graphRef.current
    if (!currentGraph) return
    // A keyword scope supersedes any BLAST scope currently on the map.
    setBlastScope(null)
    setBlastHitMap(new Map())
    setEnzymeScopeHitIds(null)
    setEnzymeSearchLoading(true)
    setSearchFocused(false)
    setNoResult(false)
    setNoResultMessage(null)
    try {
      const scope = await mapScopeSearch({ q: query, limitNodes: 90 })
      const kind = scope.kind
      const emptyGraph = scope.graph.nodes.length === 0 && scope.graph.edgeGroups.length === 0
      if (kind === 'none' || emptyGraph) {
        setScopeSearch(null)
        setNoResult(true)
        setNoResultMessage(
          kind === 'compound'
            ? `Compound "${query}" is in the library, but none of its reactions currently have a usable enzyme edge to draw on the map.`
            : kind === 'enzyme'
              ? `${scope.total} enzyme hit(s), but none currently have a usable reaction edge to draw on the map.`
              : `Nothing in the database matches "${query}". Try a compound name, enzyme name, or EC number.`,
        )
        return
      }
      if (!browseSnapshotRef.current) {
        browseSnapshotRef.current = { graph: currentGraph, positions: positionsRef.current, camera: cameraRef.current }
      }
      clearPairSelection()
      // Keep the returned neighbourhood compact: the layout above is sized for
      // the pan-around browse canvas, so re-fit it to sit inside the viewport.
      // For a compound search the searched compound is pinned to the visual
      // centre (ring + glow + always-on label) — no card over the centre.
      const anchorId = kind === 'compound' ? scope.anchorIds.find((id) => scope.graph.nodes.some((node) => node.compoundId === id)) : null
      const focusId = anchorId || (kind === 'compound' ? scope.graph.nodes[0]?.compoundId : null) || null
      const layout = createHomeLayout(scope.graph)
      const layoutPositions = fitScopeHomePositions(layout.positions, focusId)
      graphRef.current = scope.graph
      positionsRef.current = layoutPositions
      setGraph(scope.graph)
      setPositions(layoutPositions)
      setCamera({ x: 0, y: 0 })
      cameraRef.current = { x: 0, y: 0 }
      setScopeSearch({
        query,
        total: scope.total,
        shown: scope.shown,
        kind,
        anchorLabel: scope.anchorLabel || undefined,
        reactionCount: scope.reactionCount,
      })
      // An enzyme keyword scope knows exactly which enzymes were searched: those
      // ids are the "retrieved" set used to emphasise hit edges over background
      // isoenzymes when a composite edge is expanded. Compound scopes have none.
      setEnzymeScopeHitIds(kind === 'enzyme' ? new Set((scope.enzymeIds || []).filter(Boolean)) : null)
      if (kind === 'compound' && focusId) {
        setHighlightedNodeIds(new Set([focusId]))
        setHighlightedEdgeIds(new Set())
        setHighlightedEdgeGroupIds(new Set())
      }
    } catch (err) {
      setSearchFeedback(err instanceof Error ? err.message : 'Map search failed')
    } finally {
      setEnzymeSearchLoading(false)
    }
  }

  // Scoped subgraph for a completed BLAST run: draw the hit enzymes' reaction
  // neighbourhood through /graph/by-enzymes, exactly like an enzyme keyword
  // scope but driven by the hit id list (kept in E-value order).
  const runBlastScopeSearch = async (session: BlastSession) => {
    setSearchMode('enzyme')
    clearPathwayResults()
    restoreBrowseGraph()
    const currentGraph = graphRef.current
    if (!currentGraph) return
    setBlastLoading(true)
    setBlastScope(null)
    setNoResult(false)
    setNoResultMessage(null)
    try {
      const payload = session.payload
      const enzymeIds = payload.hits.map((hit) => hit.enzymeId)
      const scopeGraph = await loadGraphForEnzymes(enzymeIds, { limitNodes: 90 })
      const emptyGraph = scopeGraph.nodes.length === 0 && scopeGraph.edgeGroups.length === 0
      if (emptyGraph) {
        setBlastHitMap(new Map())
        setNoResult(true)
        setNoResultMessage('The BLAST hits do not currently have a usable reaction edge to draw on the map.')
        return
      }
      if (!browseSnapshotRef.current) {
        browseSnapshotRef.current = { graph: currentGraph, positions: positionsRef.current, camera: cameraRef.current }
      }
      clearPairSelection()
      const layout = createHomeLayout(scopeGraph)
      const layoutPositions = fitScopeHomePositions(layout.positions, null)
      graphRef.current = scopeGraph
      positionsRef.current = layoutPositions
      setGraph(scopeGraph)
      setPositions(layoutPositions)
      setCamera({ x: 0, y: 0 })
      cameraRef.current = { x: 0, y: 0 }
      setScopeSearch(null)
      setEnzymeScopeHitIds(null)
      // Best E-value per hit enzyme (hits arrive E-value-sorted, so first wins).
      const hitMap = new Map<string, BlastHit>()
      payload.hits.forEach((hit) => {
        if (!hitMap.has(hit.enzymeId)) hitMap.set(hit.enzymeId, hit)
      })
      setBlastHitMap(hitMap)
      setBlastScope({
        sessionId: session.id,
        queryLength: payload.queryLength,
        searchedSubjects: payload.searchedSubjects,
        threshold: payload.threshold,
        hits: payload.hits.length,
      })
    } catch (err) {
      setBlastScope(null)
      setBlastHitMap(new Map())
      setEnzymeScopeHitIds(null)
      setSearchFeedback(err instanceof Error ? err.message : 'Unable to draw the BLAST hits on the map.')
    } finally {
      setBlastLoading(false)
    }
  }

  const handleSearchSubmit = async () => {
    const trimmed = searchValue.trim()
    if (!trimmed) {
      if (scopeActive) clearSearchScope()
      return
    }
    if (resultMode === 'table') {
      clearSearchScope()
      onOpenSearch(trimmed)
      return
    }
    await runMapEnzymeSearch(trimmed)
  }

  // Re-entering from the table results page with a query (Map toggle): scope the
  // map to that query as soon as the browse graph has finished loading.
  useEffect(() => {
    if (!autoMapSearch) return
    if (autoSearchHandledRef.current === autoMapSearch.nonce) return
    if (loading || mapExpanding || !graph || graph.nodes.length === 0) return
    autoSearchHandledRef.current = autoMapSearch.nonce
    setResultMode('map')
    setSearchMode('enzyme')
    void runMapEnzymeSearch(autoMapSearch.query)
    onAutoMapSearchConsumed?.()
  }, [autoMapSearch, loading, mapExpanding, graph])

  // Same hand-off for a completed BLAST run: scope the map to the hit enzymes.
  useEffect(() => {
    if (!autoBlastScope || !blastSession) return
    if (blastSession.id !== autoBlastScope.sessionId) return
    if (autoBlastHandledRef.current === autoBlastScope.nonce) return
    if (loading || mapExpanding || !graph || graph.nodes.length === 0) return
    autoBlastHandledRef.current = autoBlastScope.nonce
    setResultMode('map')
    setSearchMode('enzyme')
    void runBlastScopeSearch(blastSession)
    onAutoBlastScopeConsumed?.()
  }, [autoBlastScope, blastSession, loading, mapExpanding, graph])

  /** Single-select one returned pathway card: clear any node/pair popover so the
   *  results list is the only floating panel, then highlight its chain on the map. */
  const selectPathwayCard = (card: HomePathwayCard) => {
    // Choosing a returned row is an explicit exit from 连星: stop tracing and
    // highlight the picked card instead of the in-progress chain.
    if (traceChain) stopTrace()
    setSelectedPairKey(null)
    setSelectedNodeId(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setSelectedLibraryItem(null)
    setActivePathway(card)
    setSelectedPathwayId(card.pathwayId)
    // Pathway emphasis is driven solely by activePathway (chain nodes via
    // compoundIds + edges via the pair-step keys). Do NOT fill the generic
    // .highlighted scope sets here: they match per-enzyme-edge ids, which one
    // enzyme row can share across several compound pairs, so filling them would
    // re-introduce the sibling-edge bleed this fix removes.
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setSearchFeedback(null)
    focusCameraOnPath(card.compoundIds)
  }

  /** Open the in-map single-route detail sub-view for one returned card. Swaps
   *  the map to the card's own chain graph (double-writing ref/state), keeping a
   *  snapshot of the union-results view so "返回结果" restores it exactly. */
  const openPathwayDetail = (card: HomePathwayCard) => {
    if (!pathwaySession) return
    // Opening a route detail is an explicit exit from 连星.
    if (traceChain) stopTrace()
    const chainGraph = buildPathwayChainGraph(card, pathwaySession.graph)
    if (chainGraph.nodes.length === 0) return
    const layout = createHomeLayout(chainGraph)
    const chainPositions = fitScopeHomePositions(layout.positions, null)
    // Snapshot the union view before swapping. Points are never mutated after a
    // set, so a shallow copy of the positions map is a safe restore point.
    const restore = {
      graph: graphRef.current ?? pathwaySession.graph,
      positions: { ...positionsRef.current },
      camera: { ...cameraRef.current },
    }
    clearPairSelection()
    setSelectedPathwayId(null)
    graphRef.current = chainGraph
    positionsRef.current = chainPositions
    setGraph(chainGraph)
    setPositions(chainPositions)
    setCamera({ x: 0, y: 0 })
    cameraRef.current = { x: 0, y: 0 }
    setPathwayDetail({ card, graph: chainGraph, chain: card.compoundIds, restore })
    setPickerOpen(false)
    // Highlight the chain via the existing activePathway emphasis (pair-step keys
    // are computed purely from compoundIds, so they work on the swapped graph).
    setActivePathway(card)
    setSearchFeedback(null)
  }

  /** Leave the detail sub-view: hand the union-results view back exactly as the
   *  user left it and re-highlight the card that was being viewed. */
  const closePathwayDetail = () => {
    const detail = pathwayDetail
    if (!detail) return
    graphRef.current = detail.restore.graph
    positionsRef.current = detail.restore.positions
    cameraRef.current = detail.restore.camera
    setGraph(detail.restore.graph)
    setPositions(detail.restore.positions)
    setCamera(detail.restore.camera)
    setPathwayDetail(null)
    setPickerOpen(false)
    clearPairSelection()
    // Re-highlight the card we were viewing if it still belongs to the session.
    if (pathwaySession && pathwaySession.cards.some((item) => item.pathwayId === detail.card.pathwayId)) {
      selectPathwayCard(detail.card)
    }
  }

  /* ---- per-step enzyme picker: single current step, bidirectional with the map ----
   * The picker is NOT a modal: the map stays fully operable and never closes it.
   * ``pickerStepIndex`` (owned here, not in the drawer) is the single source of
   * truth for "which step is active", so both the popup's step-bar chips and a
   * direct chain-edge click on the map switch it. ``pickerGroupEdges`` lazily
   * caches each composite step's per-enzyme fan-out (one round-trip per group) and
   * feeds both the popup candidate list and the map's expanded-edge fan-out. */

  /** Open the picker on step 1 (index 0). Group caches survive across opens. */
  const openPicker = () => {
    if (!pathwayDetail) return
    setPickerStepIndex(0)
    setPickerGroupLoading([])
    setPickerOpen(true)
  }

  /** Close the picker and leave the detail map as a clean gold chain (collapse
   *  whichever step the picker had fanned out). Never called by map interaction. */
  const closePicker = () => {
    setPickerOpen(false)
    setPickerGroupLoading([])
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setSelectedNodeId(null)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
  }

  /** Fan one route step's edge out on the map (reusing the same selectedPairKey +
   *  expandedEdges mechanics as a normal edge click) without touching the drawer.
   *  Composite steps show their per-enzyme edges once ``pickerGroupEdges`` has them;
   *  single-edge steps expand from their own step.edges immediately. */
  const expandPickerStepOnMap = (step: PathwayDetailStep) => {
    const edges = step.groupId ? pickerGroupEdges[step.groupId] : step.edges
    const key = pairKey(step.sourceId, step.targetId)
    setSelectedPairKey(key)
    setSelectedNodeId(null)
    setHighlightedNodeIds(new Set([step.sourceId, step.targetId]))
    setHighlightedEdgeGroupIds(new Set([step.groupId || key]))
    if (!edges || edges.length === 0) {
      setExpandedEdges([])
      setSelectedEdgeId(null)
    } else {
      setExpandedEdges(edges)
      setSelectedEdgeId((cur) => (cur && edges.some((edge) => edge.edgeId === cur) ? cur : (edges[0]?.edgeId ?? null)))
    }
  }

  /** Switch the active step from either the popup's step bar or a map edge click. */
  const setPickerActiveStep = (index: number) => {
    const step = detailSteps[index]
    if (!step) return
    if (index !== pickerStepIndex) setPickerStepIndex(index)
    if (pickerOpen && pathwayDetail) expandPickerStepOnMap(step)
  }

  // Lazy-load the active composite step's per-enzyme edges when the picker first
  // reaches it. Guarded by the requested ref so re-runs / StrictMode don't refetch.
  useEffect(() => {
    if (!pickerOpen || !pathwayDetail) return
    const step = detailSteps[pickerStepIndex]
    if (!step || !step.groupId) return
    const groupId = step.groupId
    if (pickerGroupRequestedRef.current.has(groupId)) return
    pickerGroupRequestedRef.current.add(groupId)
    setPickerGroupLoading((prev) => (prev.includes(groupId) ? prev : [...prev, groupId]))
    loadExpandedEdgeGroup(groupId)
      .then((edges) => {
        setPickerGroupEdges((prev) => (prev[groupId] ? prev : { ...prev, [groupId]: edges }))
      })
      .catch(() => {
        // A failed load must not poison the cache forever — allow a retry the next
        // time this step becomes active.
        pickerGroupRequestedRef.current.delete(groupId)
      })
      .finally(() => {
        setPickerGroupLoading((prev) => prev.filter((id) => id !== groupId))
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickerOpen, pathwayDetail, detailSteps, pickerStepIndex])

  // Keep the map's selection locked onto the active step while the picker is open:
  // any step change (chip or edge) — or a group fan-out finishing its load — marks
  // that step's source/target and expands its composite edge.
  useEffect(() => {
    if (!pickerOpen || !pathwayDetail) return
    const step = detailSteps[pickerStepIndex]
    if (!step) return
    expandPickerStepOnMap(step)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickerOpen, pathwayDetail, detailSteps, pickerStepIndex, pickerGroupEdges])

  /** Run a pathway-mode search. Mirrors runMapEnzymeSearch's ref/state discipline:
   *  restore the browse graph first, swap in the union payload (double-writing
   *  graph/positions/camera), then keep the returned cards + auto-select the first.
   *  Soft business errors (unknown compound / same endpoints / bad range) surface
   *  under the composer rather than replacing the map. */
  const runPathwaySearch = async (payload: PathwayComposerPayload) => {
    setSearchMode('pathway')
    clearPathwayResults()
    restoreBrowseGraph()
    const currentGraph = graphRef.current
    if (!currentGraph) return
    setPathwayError(null)
    setNoResult(false)
    setNoResultMessage(null)
    setPathwaySearchLoading(true)
    setBlastScope(null)
    setBlastHitMap(new Map())
    setEnzymeScopeHitIds(null)
    setScopeSearch(null)
    try {
      const res = await runPathwaySearchApi({
        startCompoundId: payload.startCompoundId,
        endCompoundId: payload.endCompoundId,
        viaCompoundIds: payload.viaCompoundIds,
        maxSteps: 6,
        // Backend caps results at 40; ask for the full window so long routes
        // are not silently dropped from the returned card list.
        limit: 40,
      })
      if (res.items.length === 0) {
        setNoResult(true)
        setNoResultMessage(
          `No pathway connects "${payload.startCompoundId}" to "${payload.endCompoundId}" within 6 enzyme steps. Try fewer or different intermediate compounds, or a looser endpoint pairing.`,
        )
        return
      }
      if (!browseSnapshotRef.current) {
        browseSnapshotRef.current = { graph: currentGraph, positions: positionsRef.current, camera: cameraRef.current }
      }
      clearPairSelection()
      const layout = createHomeLayout(res.graph)
      const layoutPositions = fitScopeHomePositions(layout.positions, null)
      graphRef.current = res.graph
      positionsRef.current = layoutPositions
      setGraph(res.graph)
      setPositions(layoutPositions)
      setCamera({ x: 0, y: 0 })
      cameraRef.current = { x: 0, y: 0 }
      const queryLabel = [payload.startCompoundId, ...payload.viaCompoundIds, payload.endCompoundId].join(' → ')
      setPathwaySession({ graph: res.graph, cards: res.items, total: res.total, query: queryLabel })
      // Collapse the composer so the union graph + result cards read cleanly;
      // a compact launcher pill (or clicking the toggle) brings it back.
      setComposerOpen(false)
      // Auto-select the shortest chain so the union graph starts with one
      // highlighted pathway; clicking any row switches the highlight.
      const first = res.items[0]
      if (first) selectPathwayCard(first)
    } catch (err) {
      setPathwayError(err instanceof Error ? err.message : 'Unable to search pathway.')
    } finally {
      setPathwaySearchLoading(false)
    }
  }

  const focusCameraOnPath = (compoundIds: string[]) => {
    const points = compoundIds.map((id) => positionsRef.current[id]).filter(Boolean)
    if (points.length === 0) return
    const center = {
      x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
      y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
    }
    focusCameraOnPoint(center, { x: 42, y: 56 })
  }

  const compoundImageUrl = (compound: HomeGraphCompound) => {
    const chebiId = compound.chebiId || compound.compoundId
    if (chebiId?.startsWith('CHEBI:')) return `/api/v1/assets/compounds/${encodeURIComponent(chebiId)}/structure.svg?v=4`
    return compound.structureImageUrl || null
  }
  const selectedNodeQueueEntity = selectedNode ? homeCompoundToEntity(selectedNode, compoundImageUrl(selectedNode)) : null

  const searchPlaceholder = 'Search compounds or enzymes (e.g. limonene, germacrene D synthase)'

  const selectedNeighborIds = new Set<string>()
  if (selectedNodeId) {
    selectedNeighborIds.add(selectedNodeId)
    viewModel.pairs.forEach((pair) => {
      if (pair.sourceId === selectedNodeId) selectedNeighborIds.add(pair.targetId)
      if (pair.targetId === selectedNodeId) selectedNeighborIds.add(pair.sourceId)
    })
  }
  if (selectedPair) {
    selectedNeighborIds.add(selectedPair.sourceId)
    selectedNeighborIds.add(selectedPair.targetId)
  }

  const homeMapStyle = { ['--home-label-scale' as string]: String(labelFontScale) } as CSSProperties

  return (
    <div className="home-map-page" style={homeMapStyle}>
      <section className="atlas-map-stage atlas-live-stage" aria-label="Interactive compound graph homepage">
        <header className="graph-top-nav">
          <button type="button" className="atlas-brand" onClick={handleBrandHome} title="Back to the Atlas home map" aria-label="Starase Atlas home">
            <span className="atlas-logo">
              <Network size={18} />
            </span>
            <span>Starase Atlas</span>
          </button>

          <div className={`home-search-bar ${searchMode === 'pathway' ? 'pathway-mode' : ''}`}>
            <div className="home-mode-toggle" role="group" aria-label="Map search mode">
              <button type="button" className={searchMode === 'enzyme' ? 'is-active' : ''} onClick={() => switchSearchMode('enzyme')} title="Search compounds and enzymes by keyword / BLAST">
                Enzyme
              </button>
              <button type="button" className={searchMode === 'pathway' ? 'is-active' : ''} onClick={() => switchSearchMode('pathway')} title="Find compound chains from a start through optional waypoints to an end">
                Pathway
              </button>
            </div>
            {searchMode === 'enzyme' && (
              <>
                <input
                  value={searchValue}
                  onFocus={() => setSearchFocused(true)}
                  onChange={(event) => {
                    setSearchValue(event.target.value)
                    setSearchFocused(true)
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void handleSearchSubmit()
                    if (event.key === 'Escape') setSearchFocused(false)
                  }}
                  placeholder={searchPlaceholder}
                />
                <div className="home-result-toggle" role="group" aria-label="Search result view">
                  <button type="button" className={resultMode === 'map' ? 'is-active' : ''} onClick={() => setResultMode('map')}>Map</button>
                  <button type="button" className={resultMode === 'table' ? 'is-active' : ''} onClick={() => setResultMode('table')}>Table</button>
                </div>
                <button className="home-search-submit" type="button" onClick={() => void handleSearchSubmit()} title="Search">
                  <Search size={20} />
                </button>
                {showSearchSuggestions && (
                  <div className="home-search-suggestions" onPointerDown={(event) => event.preventDefault()}>
                    <div className="home-search-filter-row">
                      {homeSearchFilters.map((filter) => (
                        <button
                          key={filter.id}
                          type="button"
                          className={searchFilter === filter.id ? 'is-active' : ''}
                          onClick={() => setSearchFilter(filter.id)}
                        >
                          {filter.label}
                        </button>
                      ))}
                    </div>
                    <div className="home-search-result-list">
                      {visibleSearchSuggestions.map((suggestion) => (
                        <button key={suggestion.id} type="button" onClick={() => void handleSearchSuggestionSelect(suggestion)}>
                          <span className={`home-result-kind ${suggestion.kind}`}>{suggestion.kind}</span>
                          <span>
                            <strong>{suggestion.title}</strong>
                            <small>{suggestion.subtitle}</small>
                          </span>
                        </button>
                      ))}
                      {visibleSearchSuggestions.length === 0 && (
                        <div className="home-search-empty">
                          {librarySearchLoading ? 'Searching...' : 'No matching entries in the current map.'}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          <nav className="graph-primary-nav" aria-label="Graph page navigation">
            <button type="button" onClick={() => onOpenSearch(searchValue.trim() || undefined)}>Data Browser</button>
            <button type="button" onClick={onOpenBlast}>BLAST</button>
            <button type="button" onClick={() => setStructureOpen(true)}>Structure search</button>
            <span className="graph-user-chip">NJU - China 2026</span>
          </nav>
        </header>

        <div className="graph-title-band">
          <div className="graph-crumbs">
            <span>Home</span>
            <ChevronRight size={14} />
            <strong>Graph Atlas</strong>
          </div>
          <h1>Compound Relationship Graph: Enzymes & Pathways</h1>
        </div>

        <aside className="graph-filter-sidebar" aria-label="Graph filters and controls">
          <div className="home-filter-head">
            <p className="graph-filter-title">Filters</p>
            {anyFilterActive && (
              <button className="home-filter-reset" type="button" onClick={resetActiveFilters}>
                Reset
              </button>
            )}
          </div>

          <div className="home-filter-group">
            <button className={`home-filter-select ${speciesMenuOpen ? 'is-open' : ''}`} type="button" onClick={() => setSpeciesMenuOpen((open) => !open)} aria-expanded={speciesMenuOpen}>
              <span className="home-filter-select-label">Organism</span>
              <span className="home-filter-select-value">{activeFilters.species.length === 0 ? 'All organisms' : `${activeFilters.species.length} selected`}</span>
              <ChevronDown size={14} />
            </button>
            {speciesMenuOpen && (
              <div className="home-filter-menu">
                <div className="home-filter-search">
                  <Search size={13} />
                  <input value={speciesQuery} onChange={(event) => setSpeciesQuery(event.target.value)} placeholder="Search organisms…" autoFocus />
                </div>
                <div className="home-filter-list">
                  {speciesSelectOptions.filter((species) => species.toLowerCase().includes(speciesQuery.trim().toLowerCase())).map((species) => {
                    const checked = activeFilters.species.includes(species)
                    return (
                      <button key={species} type="button" className={checked ? 'is-checked' : ''} onClick={() => toggleSpeciesFilter(species)}>
                        <span className={`home-filter-check ${checked ? 'checked' : ''}`}>{checked && <Check size={11} />}</span>
                        <span className="home-filter-option-label">{species}</span>
                      </button>
                    )
                  })}
                  {speciesSelectOptions.length === 0 && <div className="home-filter-empty">No organism data yet.</div>}
                </div>
              </div>
            )}
            {activeFilters.species.length > 0 && (
              <div className="home-chip-row home-species-chips">
                {activeFilters.species.map((species) => (
                  <button key={species} type="button" className="home-chip on" onClick={() => toggleSpeciesFilter(species)} title={`Remove ${species}`}>
                    {species} <X size={11} />
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="home-filter-group">
            <p className="home-filter-label">Data source</p>
            <div className="home-chip-row home-source-chips">
              {HOME_SOURCE_ORDER.filter((key) => HOME_SOURCE_LABELS[key]).map((key) => {
                const checked = activeFilters.sourceTypes.includes(key)
                return (
                  <button key={key} type="button" className={`home-chip ${checked ? 'on' : ''}`} onClick={() => toggleSourceFilter(key)} title={checked ? `Remove ${homeSourceLabel(key)}` : `Filter by ${homeSourceLabel(key)}`}>
                    {homeSourceLabel(key)}
                  </button>
                )
              })}
            </div>
          </div>

          <div className={`floating-pill mapping-pill ${controlsOpen ? 'is-open' : ''}`}>
            <button type="button" onClick={() => setControlsOpen((open) => !open)}>
              <span>Graph controls</span>
              <ChevronDown size={16} />
            </button>
            {controlsOpen && (
              <div className="floating-menu source-menu compact-home-menu control-home-menu">
                <div className="control-group">
                  <label htmlFor="home-node-size">Node size</label>
                  <div className="control-slider-row">
                    <input id="home-node-size" className="control-slider" type="range" min="0.7" max="2.8" step="0.05" value={nodeSize} onChange={(event) => setNodeSize(Number(event.target.value))} />
                    <span className="control-value">{nodeSize.toFixed(1)}</span>
                  </div>
                </div>
                <div className="control-group">
                  <label htmlFor="home-edge-thickness">Edge thickness</label>
                  <div className="control-slider-row">
                    <input id="home-edge-thickness" className="control-slider" type="range" min="0.5" max="2.4" step="0.05" value={edgeThickness} onChange={(event) => setEdgeThickness(Number(event.target.value))} />
                    <span className="control-value">{edgeThickness.toFixed(2)}</span>
                  </div>
                </div>
                <div className="control-group">
                  <label htmlFor="home-label-font">Label font size</label>
                  <div className="control-slider-row">
                    <input id="home-label-font" className="control-slider" type="range" min="0.6" max="2.6" step="0.05" value={labelFontScale} onChange={(event) => setLabelFontScale(Number(event.target.value))} />
                    <span className="control-value">{labelFontScale.toFixed(2)}×</span>
                  </div>
                </div>
                <div className="control-menu-actions">
                  <button type="button" onClick={() => { resetLayout(); setControlsOpen(false) }}>Reset layout</button>
                  <button type="button" onClick={() => { clearPairSelection(); setControlsOpen(false) }}>Clear selection</button>
                  <button type="button" onClick={() => { onOpenSearch(searchValue.trim() || undefined); setControlsOpen(false) }}>Open search library</button>
                </div>
              </div>
            )}
          </div>

          <button className="floating-pill download-pill home-pill-button" type="button" onClick={onOpenDownloads}>
            Downloading table
            {queueCount > 0 && <span>{queueCount}</span>}
          </button>
        </aside>

        {loading && <div className="home-map-feedback"><Loader2 size={18} className="spin" /> Loading backend graph...</div>}
        {error && !loading && <div className="home-map-feedback error-state"><X size={18} /> {error}</div>}
        {mapExpanding && !loading && !error && <div className="home-map-feedback map-expanding-feedback"><Loader2 size={18} className="spin" /> Expanding map...</div>}
        {searchFeedback && !loading && !error && <div className="home-search-feedback">{searchFeedback}</div>}
        {enzymeSearchLoading && !loading && (
          <div className="home-scope-feedback scope-loading">
            <Loader2 size={15} className="spin" /> Searching the library…
          </div>
        )}
        {blastLoading && !loading && (
          <div className="home-scope-feedback scope-loading">
            <Loader2 size={15} className="spin" /> Drawing BLAST hits on the map…
          </div>
        )}
        {searchMode === 'enzyme' && scopeSearch && !enzymeSearchLoading && (
          <div className="home-scope-feedback">
            <strong className="scope-query">“{scopeSearch.query}”</strong>
            {scopeSearch.kind === 'compound' ? (
              <span className="scope-count">
                compound scope{scopeSearch.shown > 0 && scopeSearch.total > 0 ? ` · ${scopeSearch.shown}/${scopeSearch.total} family compound${scopeSearch.total === 1 ? '' : 's'}` : ''}
                {scopeSearch.reactionCount ? ` · ${scopeSearch.reactionCount} reaction${scopeSearch.reactionCount === 1 ? '' : 's'}` : ''}
              </span>
            ) : (
              <span className="scope-count">
                {scopeSearch.total} enzyme hit{scopeSearch.total === 1 ? '' : 's'} · top {scopeSearch.shown} on the map{scopeSearch.total > scopeSearch.shown ? ` · ${scopeSearch.total - scopeSearch.shown} more in the table` : ''}
              </span>
            )}
            {scopeSearch.kind === 'enzyme' && (
              <button className="home-scope-action" type="button" onClick={() => onOpenSearch(scopeSearch.query)}>Open table</button>
            )}
            <button className="home-scope-action ghost" type="button" onClick={clearSearchScope}>Clear search</button>
          </div>
        )}
        {searchMode === 'enzyme' && blastScope && !blastLoading && (
          <div className="home-scope-feedback blast-scope-feedback">
            <strong className="scope-query">BLASTp</strong>
            <span className="scope-count">
              {blastScope.hits} hit{blastScope.hits === 1 ? '' : 's'} · query {blastScope.queryLength} aa · threshold E-value ≤ {blastScope.threshold === 10 ? '10' : blastScope.threshold.toExponential(0)} · {blastScope.searchedSubjects} subjects
            </span>
            <button className="home-scope-action" type="button" onClick={onOpenBlastTable}>Table results</button>
            <button className="home-scope-action ghost" type="button" onClick={clearSearchScope}>Clear search</button>
          </div>
        )}
        {noResult && !enzymeSearchLoading && (
          <div className="home-noresult-panel" role="status">
            <h3>{searchMode === 'pathway' ? 'No pathway found' : 'Nothing matched on the map'}</h3>
            <p>{noResultMessage || (searchMode === 'pathway' ? 'Try different start/end compounds or fewer intermediate steps.' : 'Try a different compound name, enzyme name, EC number, or organism.')}</p>
            <div className="home-noresult-actions">
              {searchMode === 'pathway' ? (
                <button className="home-scope-action ghost" type="button" onClick={clearSearchScope}>Back to browse map</button>
              ) : (
                <button className="home-scope-action" type="button" onClick={() => onOpenSearch(searchValue.trim() || undefined)}>Search table view</button>
              )}
              <button className="home-scope-action ghost" type="button" onClick={clearSearchScope}>Clear search</button>
            </div>
          </div>
        )}

        {searchMode === 'pathway' && !loading && !error && graph && !detailOpen && (composerOpen ? (
          <PathwaySearchComposer
            busy={pathwaySearchLoading}
            externalError={pathwayError}
            onRun={(payload) => void runPathwaySearch(payload)}
            onDismissError={() => setPathwayError(null)}
            onCollapse={() => setComposerOpen(false)}
          />
        ) : traceChain ? null : (
          <button
            className="pw-composer-launcher"
            type="button"
            onClick={() => setComposerOpen(true)}
            title="Show pathway search"
          >
            <Route size={15} />
            <span>Pathway search</span>
          </button>
        ))}

        {/* 连星 floats directly beneath the composer pill (not in the results
            header) so it never crowds the card list; it only makes sense once a
            pathway session has results to trace across. */}
        {searchMode === 'pathway' && !loading && !error && graph && !detailOpen && pathwaySession && !composerOpen && !traceChain && (
          <button
            className="trace-start-button"
            type="button"
            onClick={beginTrace}
            disabled={Boolean(traceChain)}
            title="Trace a route on the map: click compounds connected to the start, one step at a time, until you reach the end compound"
          >
            <Link2 size={13} />
            <span>连星</span>
          </button>
        )}

        {traceChain && searchMode === 'pathway' && !loading && !error && graph && !detailOpen && (
          <div className="pw-trace-bar" role="status" aria-live="polite">
            <span className="pw-trace-strong">
              <Link2 size={13} />
              <strong>{traceChain.length} node{traceChain.length === 1 ? '' : 's'}</strong>
            </span>
            <span className="pw-trace-current" title="Current compound">{compoundName(traceCurrentId || '')}</span>
            {traceHint ? (
              <span className="pw-trace-hint is-error">{traceHint}</span>
            ) : (
              <span className="pw-trace-guide">
                Click a ringed compound connected to the current one{compoundName(traceEndId || '') ? <> — finish by reaching <strong>{compoundName(traceEndId || '')}</strong></> : null}. Revisiting a node is not allowed.
              </span>
            )}
            <button type="button" className="pw-trace-undo" onClick={undoTrace} disabled={!traceChain || traceChain.length <= 1} title="Undo the last pick">
              <ArrowLeft size={13} />
              <span>Undo</span>
            </button>
            <button type="button" className="pw-trace-cancel" onClick={cancelTrace} title="Cancel tracing and restore the previous selection">
              <X size={13} />
              <span>Cancel</span>
            </button>
          </div>
        )}

        {!loading && !error && graph && (
          <svg
            ref={svgRef}
            className="home-map-svg home-live-map"
            viewBox={`0 0 ${HOME_VIEWBOX_WIDTH} ${HOME_VIEWBOX_HEIGHT}`}
            role="img"
            aria-label="Draggable compound graph"
            onPointerDown={handleMapPointerDown}
            onPointerMove={handleMapPointerMove}
            onPointerUp={finishMapPan}
            onPointerCancel={finishMapPan}
          >
            <defs>
              <marker id="home-map-arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" fill="rgba(249, 238, 201, 0.82)" />
              </marker>
              <filter id="home-node-glow" x="-60%" y="-60%" width="220%" height="220%">
                <feDropShadow dx="0" dy="0" stdDeviation="0.62" floodColor="rgba(247, 240, 214, 0.54)" />
              </filter>
              <filter id="selected-node-glow" x="-80%" y="-80%" width="260%" height="260%">
                <feDropShadow dx="0" dy="0" stdDeviation="0.95" floodColor="rgba(250, 214, 242, 0.72)" />
              </filter>
            </defs>
            <rect className="home-map-pan-layer" x="0" y="0" width={HOME_VIEWBOX_WIDTH} height={HOME_VIEWBOX_HEIGHT} />

            <g className="home-map-camera" transform={`translate(${camera.x} ${camera.y})`}>
              <g className="home-map-edges live-map-edges">
                {viewModel.pairs.map((pair) => {
                  const source = positions[pair.sourceId]
                  const target = positions[pair.targetId]
                  if (!source || !target) return null
                  const pairMeta = anyFilterActive ? pairFilterMeta.get(pair.key) : undefined
                  if (pairMeta && !pairMeta.visible) return null
                  const pairGroupId = pair.edgeGroupId || pair.key
                  const isExpanded = selectedPairKey === pair.key && pairEdges.length > 0
                  const expandedItems = expandedEdgeGroups
                  const offsets = expandedItems.length > 1 ? expandedItems.map((_, index) => (index - (expandedItems.length - 1) / 2) * 5.2) : [0]
                  const displayCount = pairMeta ? pairMeta.passing : pair.count
                  // When a composite is filtered down to a single surviving enzyme, that enzyme's
                  // accession is the one to annotate the collapsed line with.
                  const filteredSingle = displayCount === 1 && anyFilterActive && pair.edgeGroupId
                    ? homePairPassingUnits(pair, groupItemMap, activeFilters)[0]
                    : undefined
                  const singleUnit = filteredSingle || pair.edges[0]
                  const singleUnitAccession = singleUnit ? homeUnitAccession(singleUnit) : null
                  const pairLineLabel = displayCount > 1 ? `enzyme*${displayCount}` : (singleUnitAccession || pair.edges[0]?.card?.primaryName || 'enzyme')
                  const highlightedPair = highlightedEdgeGroupIds.has(pairGroupId) || pair.edgeIds.some((edgeId) => highlightedEdgeIds.has(edgeId))
                  const pathwayPair = Boolean(activePathway && activePathwayStepKeys?.has(pair.key))
                  const pairActive = selectedPairKey === pair.key
                  const showPairLabel = pairActive || highlightedPair || pathwayPair
                  const collapsedStroke = (pairActive || highlightedPair || pathwayPair ? 0.42 : 0.28) * edgeThickness
                  // In a scoped result, expanding a composite can surface background
                  // isoenzymes alongside the enzymes that were actually searched. Tag
                  // each expanded line as retrieved (the searched enzymes) or not so
                  // the searched edges can be drawn clearly stronger.
                  const scopedRender = Boolean(scopeHitSet)
                  const retrievedFlags = scopedRender ? expandedItems.map((group) => scopeHitSet!.has(group.enzymeId)) : null
                  const retrievedColorOf = retrievedFlags ? new Array<number>(expandedItems.length).fill(0) : null
                  if (retrievedFlags && retrievedColorOf) {
                    let run = 0
                    retrievedFlags.forEach((isRetrieved, index) => {
                      if (isRetrieved) {
                        retrievedColorOf[index] = run % 4
                        run += 1
                      }
                    })
                  }
                  return (
                    <g key={pair.key} className="home-map-edge-group">
                      {!isExpanded && (
                        <>
                          <path
                            d={edgePath(source, target, 0)}
                            className={`home-map-path ${displayCount > 1 ? 'multi' : ''} ${pairActive ? 'active' : ''} ${highlightedPair ? 'highlighted' : ''} ${pathwayPair ? 'pathway' : ''}`}
                            style={{ strokeWidth: collapsedStroke }}
                            markerEnd="url(#home-map-arrow)"
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={(event) => { event.stopPropagation(); void handlePairClick(pair) }}
                          />
                          <path d={edgePath(source, target, 0)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); void handlePairClick(pair) }} />
                          <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 1.8} className={`home-edge-label ${showPairLabel ? 'is-visible' : ''}`}>{pairLineLabel}</text>
                        </>
                      )}
                      {isExpanded && expandedItems.map((edge, index) => {
                        const offset = offsets[index] ?? 0
                        const highlightedEdge = highlightedPair || edge.edgeIds.some((edgeId) => highlightedEdgeIds.has(edgeId))
                        const pathwayEdge = Boolean(activePathway && activePathwayStepKeys?.has(pair.key))
                        const selectedEdgeGroup = edge.edgeIds.includes(selectedEdgeId || '')
                        const retrieved = retrievedFlags ? retrievedFlags[index] : true
                        const isBackground = scopedRender && !retrieved
                        // Retrieved (searched) single edges keep the saturated reaction
                        // hues (rotated among the first four so neighbouring hits stay
                        // distinguishable); background isoenzymes collapse to the plain
                        // `scope-background` tone.
                        const reactionColorClass = scopedRender
                          ? (retrieved ? `reaction-color-${retrievedColorOf![index]}` : 'scope-background')
                          : (expandedItems.length === 1 ? 'single-reaction' : `reaction-color-${index % 10}`)
                        const edgeEmphasized = selectedEdgeGroup || highlightedEdge || pathwayEdge
                        const expandedStroke = !scopedRender
                          ? (edgeEmphasized ? 0.52 : 0.46) * edgeThickness
                          : retrieved
                            ? (edgeEmphasized ? 0.72 : 0.6) * edgeThickness
                            : (edgeEmphasized ? 0.5 : 0.34) * edgeThickness
                        return (
                          <g key={edge.key} className={isBackground ? 'scope-miss-edge' : (scopedRender ? 'scope-hit-edge' : undefined)}>
                            <path
                              d={edgePath(source, target, offset)}
                              className={`expanded-edge live-expanded-edge ${reactionColorClass} ${scopedRender && retrieved ? 'scope-retrieved' : ''} ${isBackground ? 'scope-background' : ''} ${selectedEdgeGroup ? 'selected' : ''} ${highlightedEdge ? 'highlighted' : ''} ${pathwayEdge ? 'pathway' : ''}`}
                              style={{ strokeWidth: expandedStroke }}
                              markerStart={edge.directionMode === 'reverse' || edge.directionMode === 'bidirectional' ? 'url(#home-map-arrow)' : undefined}
                              markerEnd={edge.directionMode === 'forward' || edge.directionMode === 'bidirectional' ? 'url(#home-map-arrow)' : undefined}
                              onPointerDown={(event) => event.stopPropagation()}
                              onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.representative.edgeId) }}
                            />
                            <path d={edgePath(source, target, offset)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.representative.edgeId) }} />
                            <text x={(source.x + target.x) / 2 + offset * 0.34} y={(source.y + target.y) / 2 + offset * 0.45 - 1.4} className="expanded-edge-label">{edge.label}</text>
                          </g>
                        )
                      })}
                    </g>
                  )
                })}
              </g>

              <g className="home-map-nodes">
                {viewModel.nodes.map((node) => {
                  const pairEndpoint = selectedPair?.sourceId === node.compoundId || selectedPair?.targetId === node.compoundId
                  const selected = node.compoundId === selectedNodeId
                  const highlighted = highlightedNodeIds.has(node.compoundId)
                  const pathway = Boolean(activePathway?.compoundIds.includes(node.compoundId))
                  const neighbor = selectedNeighborIds.has(node.compoundId) && !selected && !pairEndpoint
                  const pos = positions[node.compoundId]
                  if (!pos) return null
                  const traceCurrent = Boolean(traceChain && node.compoundId === traceCurrentId)
                  const traceReachable = Boolean(traceChain && traceNextIds?.has(node.compoundId))
                  const traceGoal = Boolean(traceChain && node.compoundId === traceEndId)
                  const emphasized = selected || highlighted || pathway || pairEndpoint
                  const displayNodeSize = nodeSize * (emphasized ? 1.14 : neighbor ? 1.06 : 1)
                  const showNodeLabel = importantLabelIds.has(node.compoundId) || emphasized || neighbor || Boolean(traceChain && (traceReachable || traceGoal))
                  return (
                    <g key={node.compoundId} className={`home-map-node ${selected || pairEndpoint ? 'selected' : ''} ${highlighted ? 'highlighted' : ''} ${pathway ? 'pathway' : ''} ${neighbor ? 'neighbor' : ''} ${traceCurrent ? 'trace-current' : ''} ${traceReachable ? 'trace-reachable' : ''} ${traceGoal ? 'trace-goal' : ''} ${activeNodeDragId === node.compoundId ? 'dragging' : ''}`}>
                      {emphasized && (
                        <circle
                          className="selected-ring"
                          cx={pos.x}
                          cy={pos.y}
                          r={displayNodeSize + 0.82}
                        />
                      )}
                      <circle
                        cx={pos.x}
                        cy={pos.y}
                        r={displayNodeSize}
                        filter={emphasized ? 'url(#selected-node-glow)' : 'url(#home-node-glow)'}
                        onPointerDown={(event) => handleNodePointerDown(event, node, pos)}
                        onPointerMove={handleNodePointerMove}
                        onPointerUp={finishNodeDrag}
                        onPointerCancel={finishNodeDrag}
                      />
                      {traceGoal && (
                        <circle className="trace-goal-ring" cx={pos.x} cy={pos.y} r={displayNodeSize + 1.45} />
                      )}
                      {traceCurrent && (
                        <circle className="trace-current-ring" cx={pos.x} cy={pos.y} r={displayNodeSize + 1.45} />
                      )}
                      {traceReachable && !traceGoal && (
                        <circle className="trace-candidate-ring" cx={pos.x} cy={pos.y} r={displayNodeSize + 0.92} />
                      )}
                      <title>{node.name}</title>
                      <text x={pos.x} y={pos.y + displayNodeSize + 6.3} className={`home-map-node-name ${showNodeLabel ? 'is-visible' : ''}`}>
                        {wrapCompoundLabel(node.name).map((line, lineIndex) => (
                          <tspan key={`${node.compoundId}:label:${lineIndex}`} x={pos.x} dy={lineIndex === 0 ? 0 : '1.2em'}>{line}</tspan>
                        ))}
                      </text>
                    </g>
                  )
                })}
              </g>
            </g>
          </svg>
        )}

        {!selectedPair && selectedNode && (
          <div className="compound-popover live-compound-popover map-draggable-panel" style={panelStyle}>
            <div className="popover-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <strong>{selectedNode.name}</strong>
              <div className="popover-heading-actions">
                {selectedNode.chebiUrl ? (
                  <a className="popover-open-link" href={selectedNode.chebiUrl} target="_blank" rel="noreferrer" title="Open in ChEBI">
                    <ArrowUpRight size={20} />
                  </a>
                ) : null}
                <button className="popover-close-button" type="button" onClick={() => setSelectedNodeId(null)} title="Close compound card">
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="popover-id">ChEBI ID : {selectedNode.chebiId || selectedNode.compoundId}</div>
            <div className="compound-structure">
              {compoundImageUrl(selectedNode) ? <img src={compoundImageUrl(selectedNode) || undefined} alt={`${selectedNode.name} structure`} /> : <div className="structure-unavailable">No structure</div>}
            </div>
            <div className="popover-fields">
              <p><span>ID :</span><strong>{selectedNode.compoundId}</strong></p>
              {selectedNode.averageMass && <p><span>Mass :</span><strong>{selectedNode.averageMass}</strong></p>}
              {selectedNode.formula && <p><span>Formula :</span><strong>{selectedNode.formula}</strong></p>}
              {selectedNode.smiles && <p className="popover-smiles-row"><span>Smiles :</span><strong>{selectedNode.smiles}</strong></p>}
            </div>
            <button className="popover-cart" type="button" onClick={() => onToggleQueue(selectedNodeQueueEntity || selectedNode.compoundId)}>
              <span className={`check-box ${isQueued(selectedNode.compoundId) ? 'checked' : ''}`}>{isQueued(selectedNode.compoundId) && <Check size={17} />}</span>
              {isQueued(selectedNode.compoundId) ? 'In downloading table' : 'Add to downloading table'}
            </button>
          </div>
        )}

        {selectedPair && !pickerOpen && (
          <div className="enzyme-card-stack live-enzyme-stack map-draggable-panel" style={panelStyle}>
            <div className="stack-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <div>
                <strong>{compoundName(selectedPair.sourceId)} <ChevronRight size={14} /> {compoundName(selectedPair.targetId)}</strong>
                <span>{expandedLoading ? 'Loading enzyme paths...' : `${expandedEdgeGroups.length} enzymes · ${pairEdges.length || selectedPairTotal} edge${(pairEdges.length || selectedPairTotal) === 1 ? '' : 's'}`}</span>
              </div>
              <button className="stack-close-button" type="button" onClick={clearPairSelection} title="Close enzyme list">
                <X size={18} />
              </button>
            </div>
            {expandedEdgeGroups.map((group) => {
              const edge = group.representative
              const enzymeId = edge.card?.enzymeId || edge.enzymeId
              const blastHit = blastHitMap.get(enzymeId)
              const queued = isQueued(enzymeId)
              const queueEntity = homeEnzymeToEntity(edge, enzymeId, compoundName(edge.sourceCompoundId), compoundName(edge.targetCompoundId))
              return (
                <article key={group.key} className={`enzyme-card ${group.edgeIds.includes(selectedEdgeId || '') ? 'selected' : ''}`}>
                  <button className="card-check" type="button" onClick={() => onToggleQueue(queueEntity)}>
                    {queued ? <Check size={18} /> : <Download size={18} />}
                  </button>
                  <button className="enzyme-card-copy" type="button" onClick={() => setSelectedEdgeId(group.representative.edgeId)}>
                    <h3>{edge.card?.primaryName || edge.label}</h3>
                    {blastHit && (
                      <span className="enzyme-card-blast-chip" title="BLAST E-value">E-value {formatScopeEValue(blastHit.eValue)}</span>
                    )}
                    <p>{edge.card?.organismName || 'Unknown organism'}</p>
                    <p>{group.reactionIds.length > 1 ? `${group.reactionIds.length} reactions` : edge.card?.reactionEquation || edge.label}</p>
                    {group.reactionIds.length > 1 && <p>{group.reactionIds.slice(0, 4).join(', ')}{group.reactionIds.length > 4 ? '...' : ''}</p>}
                  </button>
                  <div className="enzyme-card-meta">
                    <strong>{group.label}</strong>
                    <span>{edge.card?.ecNumber || 'EC n/a'}</span>
                    <small>{edge.card?.databaseCode || enzymeId}</small>
                    <button type="button" onClick={() => onOpenEnzyme(enzymeId)}>Open detail</button>
                  </div>
                </article>
              )
            })}
          </div>
        )}

        {searchMode === 'pathway' && pathwaySession && !detailOpen && !selectedPair && !selectedNode && (
          <div className="pathway-result-card live-pathway-card map-draggable-panel" style={panelStyle}>
            <div className="stack-heading pathway-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <div>
                <strong>Pathway results</strong>
                <span>{pathwaySession.total} route{pathwaySession.total === 1 ? '' : 's'} · {pathwaySession.query}</span>
              </div>
              <div className="pathway-heading-actions">
                <button className="stack-close-button" type="button" onClick={clearSearchScope} title="Close pathway results and clear the search">
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="pathway-card-list">
              {pathwaySession.cards.map((card, index) => {
                const isActive = card.pathwayId === selectedPathwayId
                const firstId = card.compoundIds[0]
                const lastId = card.compoundIds[card.compoundIds.length - 1]
                const queued = isQueued(card.pathwayId)
                const queueEntity: Entity = {
                  id: card.pathwayId,
                  kind: 'pathway',
                  name: `${compoundName(firstId)} → ${compoundName(lastId)}`,
                  subtitle: `${card.stepCount} step${card.stepCount === 1 ? '' : 's'} · ${card.compoundIds.length} compound${card.compoundIds.length === 1 ? '' : 's'}`,
                  description: card.summary,
                  tags: ['Pathway'],
                  fields: [],
                  related: [],
                  pathway: {
                    startId: firstId,
                    endId: lastId,
                    compoundIds: card.compoundIds,
                    compoundNames: card.compoundIds.map((cid) => compoundName(cid)),
                    stepCount: card.stepCount,
                  },
                }
                return (
                  <div key={card.pathwayId} className={`pathway-card-row ${isActive ? 'is-active' : ''}`}>
                    <button
                      type="button"
                      className="pathway-card-main"
                      onClick={() => selectPathwayCard(card)}
                      aria-pressed={isActive}
                      title="Highlight this pathway on the map"
                    >
                      <span className="pathway-row-index">{index + 1}</span>
                      <span className="pathway-row-main">
                        <span className="pathway-row-summary">{card.summary}</span>
                        <span className="pathway-row-meta">
                          {card.stepCount} step{card.stepCount === 1 ? '' : 's'}
                          {/* segments (each source/target + edge/group id) are the
                              extension point for the upcoming pathway detail page. */}
                        </span>
                      </span>
                      <ChevronRight size={14} className="pathway-row-chevron" />
                    </button>
                    <button
                      type="button"
                      className={`pathway-queue-toggle ${queued ? 'is-queued' : ''}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        onToggleQueue(queueEntity)
                      }}
                      aria-pressed={queued}
                      title={queued ? 'Remove route from downloading table' : 'Add route to downloading table'}
                    >
                      {queued ? <Check size={14} /> : <Download size={14} />}
                    </button>
                    <button
                      type="button"
                      className="pathway-detail-button"
                      onClick={(event) => {
                        event.stopPropagation()
                        openPathwayDetail(card)
                      }}
                      aria-label={`查看路线 ${index + 1} 详情`}
                      title="查看该路线详情"
                    >
                      <ArrowUpRight size={13} />
                      <span>详情</span>
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {selectedLibraryItem && !selectedPair && !selectedNode && !activePathway && (
          <div className="library-result-card live-library-card map-draggable-panel" style={panelStyle}>
            <div className="stack-heading library-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <div>
                <strong>{selectedLibraryItem.name}</strong>
                <span>{selectedLibraryItem.subtitle}</span>
              </div>
              <button className="stack-close-button" type="button" onClick={() => setSelectedLibraryItem(null)} title="Close result card">
                <X size={18} />
              </button>
            </div>
            <p>{selectedLibraryItem.description}</p>
            <div className="library-field-list">
              {selectedLibraryItem.fields.slice(0, 6).map((field) => (
                <div key={`${selectedLibraryItem.id}:${field.label}`}>
                  <span>{field.label}</span>
                  <strong>{field.value}</strong>
                </div>
              ))}
            </div>
            {selectedLibraryItem.kind === 'enzyme' && (
              <button className="library-open-button" type="button" onClick={() => onOpenEnzyme(selectedLibraryItem.id)}>
                Open detail
              </button>
            )}
          </div>
        )}

        {detailOpen && pathwayDetail && (
          <div className="pw-detail-bar" role="region" aria-label="路线详情">
            <button className="pw-detail-back" type="button" onClick={closePathwayDetail} title="返回通路结果列表">
              <ArrowLeft size={14} />
              <span>返回结果</span>
            </button>
            <div className="pw-detail-summary">
              <strong>
                {compoundName(pathwayDetail.chain[0])}
                <span className="pw-detail-summary-arrow"> → </span>
                {compoundName(pathwayDetail.chain[pathwayDetail.chain.length - 1])}
              </strong>
              <span>
                {pathwayDetail.card.stepCount} 步 · {pathwayDetail.chain.length} 化合物
              </span>
            </div>
            <button
              className="pw-detail-download"
              type="button"
              onClick={openPicker}
              disabled={pickerOpen}
              aria-label="下载路线"
              title="为每步选择酶后加入下载表"
            >
              <Download size={14} />
              <span>下载</span>
            </button>
          </div>
        )}

        <div className="map-footer-stats home-map-stats">
          {detailOpen && pathwayDetail ? (
            <>
              <span className="home-stats-scope">Pathway detail · “{pathwaySession?.query}”</span>
              <span>Compounds in chain: {pathwayDetail.chain.length}</span>
              <span>Steps: {pathwayDetail.card.stepCount}</span>
            </>
          ) : pathwaySession ? (
            <>
              <span className="home-stats-scope">Pathways · “{pathwaySession.query}”</span>
              <span>Compounds in union: {pathwaySession.graph.nodes.length}</span>
              <span>Pathways returned: {pathwaySession.cards.length} / {pathwaySession.total}</span>
            </>
          ) : scopeSearch ? (
            <>
              <span className="home-stats-scope">
                Scope · “{scopeSearch.query}”
                {scopeSearch.kind === 'compound'
                  ? ` · compound${scopeSearch.reactionCount ? ` · ${scopeSearch.reactionCount} reactions` : ''}`
                  : ` · ${scopeSearch.total} hit(s) · top ${scopeSearch.shown}`}
              </span>
              <span>Compounds in scope: {graph?.nodes.length ?? 0}</span>
              <span>Enzyme edges in scope: {graph?.edges.length ?? 0}</span>
            </>
          ) : blastScope ? (
            <>
              <span className="home-stats-scope">Scope · BLASTp · {blastScope.hits} hit{blastScope.hits === 1 ? '' : 's'} · query {blastScope.queryLength} aa</span>
              <span>Compounds in scope: {graph?.nodes.length ?? 0}</span>
              <span>Enzyme edges in scope: {graph?.edges.length ?? 0}</span>
            </>
          ) : (
            <>
              <span>Total compounds: {graph?.nodes.length ?? 0}</span>
              <span>Total enzyme edges: {graph?.edges.length ?? 0}</span>
              <span>Visible compound pairs: {viewModel.pairs.length}</span>
              <span>Visible map edges: {visibleEdgeCount}</span>
            </>
          )}
        </div>

        <StructureSearchDrawer
          open={structureOpen}
          onClose={() => setStructureOpen(false)}
          onTransferChebi={(chebiId) => {
            // Drop the matched compound's ChEBI into the map search box only —
            // the user decides when to run it.
            setSearchValue(chebiId)
            setSearchFocused(false)
          }}
        />

        {pickerOpen && pathwayDetail && detailSteps.length > 0 && (
          <PathwayEnzymePickerDrawer
            card={pathwayDetail.card}
            steps={detailSteps}
            activeStepIndex={pickerStepIndex}
            onActiveStepChange={setPickerActiveStep}
            groupEdges={pickerGroupEdges}
            groupLoadingIds={pickerGroupLoading}
            onClose={closePicker}
            onOpenEnzyme={onOpenEnzyme}
            onAdd={(entity) => {
              onToggleQueue(entity)
              closePicker()
              setSearchFeedback(`已加入下载表：${entity.name}`)
            }}
          />
        )}
      </section>
    </div>
  )
}

/* ---------------------------------------------------------------------------
 * Pathway-mode composer: ordered start → (…via…) → end with a whole-library
 * compound dictionary autocomplete on every slot. Rendered only while
 * searchMode === 'pathway', and it owns its slot text, so toggling modes
 * resets a composition (each mount starts blank).
 * ------------------------------------------------------------------------- */
function PathwaySearchComposer({
  busy,
  externalError,
  onRun,
  onDismissError,
  onCollapse,
}: {
  busy: boolean
  externalError: string | null
  onRun: (payload: PathwayComposerPayload) => void
  onDismissError: () => void
  onCollapse: () => void
}) {
  type ComposerSlot = { id: string | null; text: string }
  type ActiveField = { field: 'start' | 'end' | 'via'; viaIndex: number }
  const newSlot = (): ComposerSlot => ({ id: null, text: '' })
  const [start, setStart] = useState<ComposerSlot>(newSlot)
  const [end, setEnd] = useState<ComposerSlot>(newSlot)
  const [vias, setVias] = useState<ComposerSlot[]>([])
  const [active, setActive] = useState<ActiveField | null>(null)
  const [suggestions, setSuggestions] = useState<CompoundSuggestion[]>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  const activeText = active
    ? active.field === 'start'
      ? start.text
      : active.field === 'end'
        ? end.text
        : vias[active.viaIndex]?.text ?? ''
    : ''

  const isSlotActive = (field: ActiveField['field'], viaIndex: number) => {
    if (!active || active.field !== field) return false
    return field !== 'via' || active.viaIndex === viaIndex
  }

  const updateStartText = (value: string) => {
    setStart({ id: null, text: value })
    onDismissError()
  }
  const updateEndText = (value: string) => {
    setEnd({ id: null, text: value })
    onDismissError()
  }
  const updateViaText = (index: number, value: string) => {
    setVias((prev) => prev.map((via, itemIndex) => (itemIndex === index ? { id: null, text: value } : via)))
    onDismissError()
  }

  const pickSuggestion = (item: CompoundSuggestion) => {
    if (!active) return
    const slot = { id: item.compoundId, text: item.name }
    if (active.field === 'start') setStart(slot)
    else if (active.field === 'end') setEnd(slot)
    else {
      setVias((prev) => prev.map((via, itemIndex) => (itemIndex === active.viaIndex ? slot : via)))
    }
    setActive(null)
    setSuggestions([])
    onDismissError()
  }

  const addVia = () => {
    setVias((prev) => [...prev, newSlot()])
    onDismissError()
  }

  const removeVia = (index: number) => {
    setVias((prev) => prev.filter((_, itemIndex) => itemIndex !== index))
    if (active?.field === 'via' && active.viaIndex === index) setActive(null)
    onDismissError()
  }

  const canRun = !busy && start.text.trim().length > 0 && end.text.trim().length > 0

  const submit = () => {
    if (!canRun) return
    onDismissError()
    onRun({
      startCompoundId: start.id || start.text.trim(),
      endCompoundId: end.id || end.text.trim(),
      viaCompoundIds: vias.map((via) => (via.id || via.text.trim())).filter((value) => value.length > 0),
    })
  }

  const handleKeyDown = (event: { key: string }) => {
    if (event.key === 'Enter') {
      if (suggestions.length > 0) {
        pickSuggestion(suggestions[0])
      } else {
        // Raw typed token: let the server resolve it on Run.
        setActive(null)
        setSuggestions([])
      }
      return
    }
    if (event.key === 'Escape') {
      setActive(null)
      setSuggestions([])
    }
  }

  useEffect(() => {
    const query = activeText.trim()
    if (!active || query.length < 2) {
      setSuggestions([])
      setSuggestionsLoading(false)
      return
    }
    let cancelled = false
    setSuggestionsLoading(true)
    const timer = window.setTimeout(() => {
      suggestCompounds(query, 10)
        .then((items) => {
          if (cancelled) return
          setSuggestions(items)
        })
        .catch(() => {
          if (!cancelled) setSuggestions([])
        })
        .finally(() => {
          if (!cancelled) setSuggestionsLoading(false)
        })
    }, 180)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [active, activeText])

  const renderSlot = (
    key: string,
    badge: string,
    field: ActiveField,
    value: string,
    placeholder: string,
    onChange: (value: string) => void,
    onRemove?: () => void,
  ) => (
    <div className={`pw-slot-chip ${onRemove ? 'is-removable' : ''}`} key={key}>
      <span className="pw-slot-badge">{badge}</span>
      <div className="pw-slot-control">
        <input
          className="pw-slot-input"
          value={value}
          placeholder={placeholder}
          onFocus={() => setActive(field)}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        {isSlotActive(field.field, field.viaIndex) && activeText.trim().length >= 2 && (
          <PathwaySuggestions items={suggestions} loading={suggestionsLoading} onPick={pickSuggestion} />
        )}
      </div>
      {onRemove && (
        <button className="pw-slot-remove" type="button" onClick={onRemove} title="Remove this intermediate" aria-label="Remove this intermediate">
          <X size={14} />
        </button>
      )}
    </div>
  )

  return (
    <div
      className="home-pathway-composer"
      onBlur={(event) => {
        const next = event.relatedTarget
        if (!(next instanceof Node) || !event.currentTarget.contains(next)) {
          setActive(null)
          setSuggestions([])
        }
      }}
    >
      <span className="pw-composer-title">Pathway</span>
      <button
        className="pw-composer-collapse"
        type="button"
        onClick={onCollapse}
        title="Hide pathway search bar"
        aria-label="Hide pathway search bar"
      >
        <ChevronsUp size={15} />
      </button>
      {renderSlot('slot:start', 'Start', { field: 'start', viaIndex: -1 }, start.text, 'Start compound (name / id / ChEBI)', updateStartText)}
      {vias.map((via, index) =>
        renderSlot(
          `slot:via:${index}`,
          `Via ${index + 1}`,
          { field: 'via', viaIndex: index },
          via.text,
          'Pass through…',
          (value) => updateViaText(index, value),
          () => removeVia(index),
        ),
      )}
      {renderSlot('slot:end', 'End', { field: 'end', viaIndex: -1 }, end.text, 'End compound (name / id / ChEBI)', updateEndText)}
      <div className="pw-composer-actions">
        <button className="pw-add-via" type="button" onClick={addVia} title="Add an intermediate compound the chain must pass through">
          <Plus size={14} /> Add via
        </button>
        <span className="pw-composer-hint">≤ 6 steps · via order kept</span>
        <button className="pw-run" type="button" disabled={!canRun} onClick={submit} title="Run pathway search">
          {busy ? <Loader2 size={15} className="spin" /> : null}
          {busy ? 'Searching…' : 'Find pathways'}
        </button>
      </div>
      {externalError && (
        <div className="pw-composer-status is-error" role="status">
          <X size={13} /> {externalError}
        </div>
      )}
    </div>
  )
}

/** Per-slot autocomplete dropdown fed by GET /compounds/suggest. */
function PathwaySuggestions({
  items,
  loading,
  onPick,
}: {
  items: CompoundSuggestion[]
  loading: boolean
  onPick: (item: CompoundSuggestion) => void
}) {
  if (loading && items.length === 0) {
    return (
      <div className="pw-slot-suggestions" onPointerDown={(event) => event.preventDefault()}>
        <div className="pw-suggest-placeholder"><Loader2 size={13} className="spin" /> Looking up compounds…</div>
      </div>
    )
  }
  return (
    <div className="pw-slot-suggestions" onPointerDown={(event) => event.preventDefault()}>
      {items.length > 0 ? (
        items.map((item) => (
          <button key={item.compoundId} type="button" onClick={() => onPick(item)}>
            <strong>{item.name}</strong>
            <small>{item.chebiId || item.compoundId}</small>
          </button>
        ))
      ) : (
        <div className="pw-suggest-placeholder">No matching compounds in the library.</div>
      )}
    </div>
  )
}

export function EnzymeDetailView({ enzymeId, onBack, onToggleQueue, isQueued }: { enzymeId: string | null; onBack: () => void; onToggleQueue: (id: string) => void; isQueued: (id: string) => boolean }) {
  const [detail, setDetail] = useState<EnzymeDetailData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloadState, setDownloadState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')

  useEffect(() => {
    if (!enzymeId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setDownloadState('idle')
    loadEnzymeDetail(enzymeId)
      .then((payload) => { if (!cancelled) setDetail(payload) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load enzyme detail') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [enzymeId])

  if (!enzymeId) return <div className="detail-page empty-detail-page"><div className="detail-empty-card"><Dna size={30} /><h2>No enzyme selected</h2><button className="primary-button" type="button" onClick={onBack}><ArrowLeft size={15} /> Back home</button></div></div>

  const queued = isQueued(enzymeId)
  const names = detail ? [detail.primaryName, ...detail.secondaryNames].filter(Boolean) : []
  const sequenceRows = detail?.sequence ? formatSequenceRows(detail.sequence) : []
  const sequenceLength = detail?.length || detail?.sequence?.length || null
  const groupedSequenceLinks = groupSequenceLinks(detail?.sequenceLinks || [])
  const handleDownload = async () => {
    if (!detail) return
    setDownloadState('loading')
    try {
      const payload = await createEnzymeDownload(detail.enzymeId, detail.primaryName)
      if (payload.fileUrl) {
        window.open(payload.fileUrl, '_blank', 'noopener,noreferrer')
        setDownloadState('ready')
      } else {
        setDownloadState('error')
      }
    } catch {
      setDownloadState('error')
    }
  }

  return (
    <div className="enzyme-detail-page">
      <section className="detail-atlas-hero">
        <div>
          <div className="eyebrow"><Dna size={14} /> Enzyme detail</div>
          <h1>{detail?.primaryName || enzymeId}</h1>
          <p>{detail?.organismName || 'Loading detail from the backend...'}</p>
        </div>
        <div className="detail-hero-actions atlas-detail-actions">
          <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={15} /> Back</button>
          <button className="secondary-button" type="button" onClick={() => onToggleQueue(enzymeId)}>{queued ? <Check size={15} /> : <Download size={15} />}{queued ? 'Queued' : 'Download'}</button>
          <button className="secondary-button" type="button" onClick={handleDownload} disabled={downloadState === 'loading'}>{downloadState === 'loading' ? <Loader2 size={15} className="spin" /> : <Download size={15} />} Export record</button>
        </div>
      </section>

      {loading && <div className="detail-status"><Loader2 size={18} className="spin" /> Loading enzyme detail...</div>}
      {error && <div className="detail-status error-state"><X size={18} /> {error}</div>}
      {detail && (
        <div className="enzyme-detail-grid">
          <section className="detail-card main-detail-card">
            <div className="detail-card-topline">
              <span className="detail-chip"><Link2 size={13} /> {detail.databaseCode}</span>
              {detail.uniprotId && <a className="detail-link" href={`https://www.uniprot.org/uniprotkb/${detail.uniprotId}`} target="_blank" rel="noreferrer">UniProt {detail.uniprotId} <ExternalLink size={12} /></a>}
            </div>
            <div className="detail-name-stack"><h2>{detail.primaryName}</h2><p>{detail.organismName || 'Unknown organism'}</p></div>
            <div className="tag-row compact">{names.map((name) => <span key={name} className="tag">{name}</span>)}</div>
            <dl className="detail-facts">
              <div><dt>Library code</dt><dd>{detail.databaseCode}</dd></div>
              <div><dt>Species</dt><dd>{detail.organismName || 'n/a'}</dd></div>
              <div><dt>UniProt</dt><dd>{detail.uniprotId || 'n/a'}</dd></div>
              <div><dt>Length</dt><dd>{sequenceLength ? `${sequenceLength} aa` : 'n/a'}</dd></div>
              <div><dt>Mass (Da)</dt><dd>{detail.mass ? Math.round(detail.mass).toLocaleString() : 'n/a'}</dd></div>
            </dl>
          </section>

          <section className="detail-card detail-stack-card">
            <div className="section-title-row"><h3>Gene</h3></div>
            {detail.gene ? (
              <div className="detail-copy-list">
                <div><span>Gene name</span><strong>{detail.gene.geneName || 'n/a'}</strong></div>
                <div><span>GenBank</span><strong>{detail.gene.genbankId || 'n/a'}</strong></div>
                <div><span>ENA accession</span><strong>{detail.gene.enaAccession || 'n/a'}</strong></div>
                <div><span>Protein accession</span><strong>{detail.gene.proteinAccession || 'n/a'}</strong></div>
              </div>
            ) : <p className="muted-copy">No gene record available.</p>}
          </section>

          <section className="detail-card detail-stack-card sequence-links-card">
            <div className="section-title-row"><h3>Sequence links</h3></div>
            {groupedSequenceLinks.length > 0 ? (
              <div className="sequence-link-groups">
                {groupedSequenceLinks.map((group) => (
                  <div key={group.category} className="sequence-link-group">
                    <span>{group.category}</span>
                    <div>
                      {group.links.map((link) => (
                        <a key={`${link.category}:${link.accession}:${link.relatedAccession || ''}`} href={link.url || link.relatedUrl || '#'} target="_blank" rel="noreferrer">
                          <strong>{link.accession}</strong>
                          {link.relatedAccession && <small>{link.relatedAccession}</small>}
                          <ExternalLink size={12} />
                        </a>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : <p className="muted-copy">No sequence links available.</p>}
          </section>

          <section className="detail-card detail-stack-card amino-sequence-card">
            <div className="section-title-row">
              <h3>Amino acid sequence</h3>
              {detail.sequence && <button className="small-text-button" type="button" onClick={() => void navigator.clipboard?.writeText(detail.sequence || '')}>Copy</button>}
            </div>
            {detail.sequence ? (
              <>
                <div className="sequence-summary">
                  <div><span>Length</span><strong>{sequenceLength || detail.sequence.length}</strong></div>
                  <div><span>Mass (Da)</span><strong>{detail.mass ? Math.round(detail.mass).toLocaleString() : 'n/a'}</strong></div>
                </div>
                <div className="amino-sequence-view" aria-label="Amino acid sequence">
                  {sequenceRows.map((row) => (
                    <div key={row.start} className="amino-sequence-row">
                      <div className="sequence-ruler"><span />{row.chunks.map((chunk, index) => <span key={`${row.start}:${index}`}>{row.start + index * 10 + chunk.length - 1}</span>)}</div>
                      <div className="sequence-line"><span>{row.start}</span><code>{row.chunks.join(' ')}</code></div>
                    </div>
                  ))}
                </div>
              </>
            ) : <p className="muted-copy">No amino acid sequence available.</p>}
          </section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Evidence</h3></div><div className="detail-reference-list">{detail.evidence.length > 0 ? detail.evidence.map((item, index) => <div key={`${item.doi || item.pubmedId || index}`} className="reference-row"><div><strong>{item.sourceDescription || 'Evidence record'}</strong><p>{item.reviewStatus || 'official'}</p></div><div className="reference-links">{item.doi && <a href={`https://doi.org/${item.doi}`} target="_blank" rel="noreferrer">DOI</a>}{item.pubmedId && <a href={`https://pubmed.ncbi.nlm.nih.gov/${item.pubmedId}/`} target="_blank" rel="noreferrer">PubMed</a>}</div></div>) : <p className="muted-copy">No evidence links available.</p>}</div></section>

          <section className="detail-card detail-stack-card reactions-card"><div className="section-title-row"><h3>Reactions</h3></div><div className="reaction-list">{detail.reactions.map((reaction) => <article key={reaction.reactionId} className="reaction-card"><div className="reaction-card-head"><div><strong>{reaction.equation}</strong><p>{reaction.direction}</p></div>{reaction.rheaUrl ? <a href={reaction.rheaUrl} target="_blank" rel="noreferrer">{reaction.rheaId || 'Rhea'} <ExternalLink size={12} /></a> : <span>{reaction.rheaId || 'Rhea n/a'}</span>}</div><div className="reaction-meta-grid"><div><span>EC</span><strong>{reaction.ecNumber || 'n/a'}</strong></div><div><span>SMILES</span><strong>{reaction.smiles || 'n/a'}</strong></div><div><span>Source type</span><strong>{reaction.sourceType}</strong></div><div><span>Review</span><strong>{reaction.reviewStatus}</strong></div></div><div className="reaction-compounds"><div><span>Substrates</span><div className="tag-row compact">{reaction.substrates.map((compound) => <span key={compound.compoundId} className="tag">{compound.name}</span>)}</div></div><div><span>Products</span><div className="tag-row compact">{reaction.products.map((compound) => <span key={compound.compoundId} className="tag">{compound.name}</span>)}</div></div></div>{reaction.atomMapImageUrl && <div className="atom-map-wrap"><img src={reaction.atomMapImageUrl} alt={`${reaction.reactionId} atom map`} /></div>}</article>)}</div></section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Links</h3></div><div className="link-list">{detail.links.map((link) => <a key={`${link.label}:${link.url}`} href={link.url} target="_blank" rel="noreferrer"><span>{link.label}</span><ExternalLink size={12} /></a>)}</div></section>
        </div>
      )}
    </div>
  )
}

type SequenceRow = {
  start: number
  chunks: string[]
}

function homeCompoundToEntity(compound: HomeGraphCompound, imageUrl?: string | null): Entity {
  return {
    id: compound.compoundId,
    kind: 'compound',
    name: compound.name,
    subtitle: compound.chebiId || compound.compoundId,
    description: compound.description || compound.smiles || 'Compound record from the terpene pathway graph.',
    tags: ['Compound'],
    imageLabel: imageUrl || compound.chebiId ? '2D structure' : undefined,
    imageUrl: imageUrl || undefined,
    fields: [
      entityField('Formula', compound.formula),
      entityField('Average mass', compound.averageMass),
      entityField('Charge', compound.charge),
      entityField('ChEBI', compound.chebiId),
      entityField('SMILES', compound.smiles),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [],
  }
}

function homeEnzymeToEntity(edge: HomeGraphEdge, enzymeId: string, sourceName: string, targetName: string): Entity {
  const card = edge.card

  return {
    id: enzymeId,
    kind: 'enzyme',
    name: card?.primaryName || edge.label || enzymeId,
    subtitle: [card?.uniprotId || card?.databaseCode || enzymeId, card?.ecNumber].filter(Boolean).join(' · '),
    description: card?.reactionEquation || `${sourceName} -> ${targetName}`,
    tags: [card?.sourceType || edge.sourceType, card?.reviewStatus || edge.reviewStatus].filter(Boolean) as string[],
    species: card?.organismName || undefined,
    fields: [
      entityField('UniProt', card?.uniprotId),
      entityField('EC number', card?.ecNumber),
      entityField('Organism', card?.organismName),
      entityField('Gene name', card?.geneName),
      entityField('Reaction', card?.reactionId || edge.reactionId),
      entityField('Direction', card?.reactionDirection || edge.direction),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [
      { id: edge.sourceCompoundId, name: sourceName, kind: 'compound' },
      { id: edge.targetCompoundId, name: targetName, kind: 'compound' },
    ],
  }
}

function entityField(label: string, rawValue: string | number | null | undefined) {
  if (rawValue === null || rawValue === undefined || rawValue === '') return null
  return { label, value: String(rawValue) }
}

function formatSequenceRows(sequence: string): SequenceRow[] {
  const clean = sequence.replace(/\s+/g, '').toUpperCase()
  const rows: SequenceRow[] = []
  for (let index = 0; index < clean.length; index += 60) {
    const line = clean.slice(index, index + 60)
    const chunks = line.match(/.{1,10}/g) || []
    rows.push({ start: index + 1, chunks })
  }
  return rows
}

function groupSequenceLinks(links: EnzymeSequenceLink[]) {
  const grouped = new Map<string, EnzymeSequenceLink[]>()
  links.forEach((link) => {
    if (!link.accession) return
    const current = grouped.get(link.category) || []
    current.push(link)
    grouped.set(link.category, current)
  })
  return Array.from(grouped.entries()).map(([category, groupLinks]) => ({ category, links: groupLinks }))
}

const HOME_SCOPE_MARGIN_X = 5
const HOME_SCOPE_MARGIN_Y = 7
// Separation target (world units) for a modest-size scope so neighbouring node
// labels do not collide after the graph is fitted into the viewport
// (~7.5 px each on a full-width stage, close to one node label height).
// Dense blast/enzyme scopes physically cannot hold this gap for every pair
// inside one frame, so relaxScopeNodeSpacing tapers it down with node count
// (see scopeMinGapFor) rather than failing.
const HOME_SCOPE_MIN_NODE_GAP = 15

/** Pick a reachable inter-node gap for a scope of `nodeCount` compounds.
 *
 * Keeping every pair at the full base gap needs area ~ n·gap²/2 inside a fixed
 * window; past ~40 nodes that no longer fits, so we step the target down to
 * avoid the relaxation pushing most nodes against the edge (which would leave
 * border nodes on top of each other).
 */
function scopeMinGapFor(nodeCount: number): number {
  if (nodeCount <= 20) return 16
  if (nodeCount <= 36) return 14
  if (nodeCount <= 55) return 12
  if (nodeCount <= 75) return 11
  return 10
}

/** Rescale a freshly laid-out scope so its nodes fit inside the visible stage.
 *
 * The normal browse layout spreads nodes across the full (large) pan canvas,
 * which is what lets the map grow as you expand. A map-search result, though,
 * is a fixed small neighbourhood that should be readable in one frame — so we
 * re-fit it into the viewport instead of letting it sit on the wide canvas.
 * `focusId` (the searched compound, when it made it into the graph) is pinned
 * to the stage centre so the neighbourhood reads as centred on it.
 */
function fitScopeHomePositions(positions: Record<string, Point>, focusId: string | null = null) {
  const points = Object.values(positions)
  if (points.length === 0) return positions
  const minX = Math.min(...points.map((point) => point.x))
  const maxX = Math.max(...points.map((point) => point.x))
  const minY = Math.min(...points.map((point) => point.y))
  const maxY = Math.max(...points.map((point) => point.y))
  const availW = HOME_VIEWBOX_WIDTH - HOME_SCOPE_MARGIN_X * 2
  const availH = HOME_VIEWBOX_HEIGHT - HOME_SCOPE_MARGIN_Y * 2
  const focusPoint = focusId ? positions[focusId] : null
  let scale: number
  let originX: number
  let originY: number
  if (focusPoint) {
    // Pin the focus node to the centre; guarantee every neighbour stays on screen.
    const left = Math.max(focusPoint.x - minX, 0.001)
    const right = Math.max(maxX - focusPoint.x, 0.001)
    const up = Math.max(focusPoint.y - minY, 0.001)
    const down = Math.max(maxY - focusPoint.y, 0.001)
    scale = Math.min((availW / 2) / Math.max(left, right), (availH / 2) / Math.max(up, down))
    originX = focusPoint.x
    originY = focusPoint.y
  } else {
    scale = Math.min(availW / Math.max(maxX - minX, 0.001), availH / Math.max(maxY - minY, 0.001))
    originX = (minX + maxX) / 2
    originY = (minY + maxY) / 2
  }
  const targetX = HOME_VIEWBOX_WIDTH / 2
  const targetY = HOME_VIEWBOX_HEIGHT / 2
  const next: Record<string, Point> = {}
  Object.entries(positions).forEach(([compoundId, point]) => {
    next[compoundId] = { x: targetX + (point.x - originX) * scale, y: targetY + (point.y - originY) * scale }
  })
  return relaxScopeNodeSpacing(next, focusId, scopeMinGapFor(Object.keys(next).length))
}

/** Push only the too-close neighbours apart so their labels stop overlapping.
 *
 * A pure global scale change would have to stretch every edge to separate the
 * single pair that actually collides, flinging distant nodes off-screen. This
 * instead separates pairs that sit closer than `minGap`, leaving the rest of
 * the (already well-spaced) layout untouched. The focus node — the searched
 * compound pinned to the centre — is held fixed.
 *
 * Nodes pushed past the fitted window are folded back inside *every* pass:
 * clamping mid-iteration lets border-crammed neighbours keep sliding apart
 * along the window edge instead of stacking on top of one another once the
 * relaxation ends.
 */
function relaxScopeNodeSpacing(positions: Record<string, Point>, focusId: string | null, minGap: number) {
  const ids = Object.keys(positions)
  if (ids.length < 2) return positions
  const minX = HOME_SCOPE_MARGIN_X
  const maxX = HOME_VIEWBOX_WIDTH - HOME_SCOPE_MARGIN_X
  const minY = HOME_SCOPE_MARGIN_Y
  const maxY = HOME_VIEWBOX_HEIGHT - HOME_SCOPE_MARGIN_Y
  for (let iteration = 0; iteration < 220; iteration += 1) {
    let moved = false
    for (let first = 0; first < ids.length; first += 1) {
      for (let second = first + 1; second < ids.length; second += 1) {
        const a = positions[ids[first]]
        const b = positions[ids[second]]
        if (!a || !b) continue
        let dx = b.x - a.x
        let dy = b.y - a.y
        let distance = Math.hypot(dx, dy)
        if (distance < 0.001) {
          const jitter = stableJitter(`${ids[first]}:${ids[second]}:scope`)
          dx = jitter.x || 0.1
          dy = jitter.y || 0.1
          distance = Math.hypot(dx, dy)
        }
        if (distance >= minGap) continue
        const push = (minGap - distance) / 2
        const unitX = dx / distance
        const unitY = dy / distance
        if (ids[first] !== focusId) {
          a.x -= unitX * push
          a.y -= unitY * push
        }
        if (ids[second] !== focusId) {
          b.x += unitX * push
          b.y += unitY * push
        }
        moved = true
      }
    }
    if (!moved) break
    // Keep the whole neighbourhood inside the fitted window as it breathes.
    ids.forEach((compoundId) => {
      if (compoundId === focusId) return
      const point = positions[compoundId]
      if (!point) return
      point.x = Math.min(Math.max(point.x, minX), maxX)
      point.y = Math.min(Math.max(point.y, minY), maxY)
    })
  }
  return positions
}

function createHomeLayout(graph: HomeGraphData | null) {
  if (!graph || graph.nodes.length === 0) return { nodes: [] as HomeGraphCompound[], positions: {} as Record<string, Point>, pairs: [] as PairEntry[] }
  const score = buildHomeDegreeScore(graph)
  const nodes = [...graph.nodes].sort((a, b) => (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name))
  const positions = createInitialHomePositions(nodes, graph)
  const visibleIds = new Set(nodes.map((node) => node.compoundId))
  return { nodes, positions, pairs: buildHomePairs(graph, visibleIds) }
}

function buildHomePairs(graph: HomeGraphData, visibleIds: Set<string>) {
  const pairMap = new Map<string, PairEntry>()
  graph.edgeGroups.forEach((group) => {
    if (!visibleIds.has(group.sourceCompoundId) || !visibleIds.has(group.targetCompoundId)) return
    pairMap.set(pairKey(group.sourceCompoundId, group.targetCompoundId), { key: pairKey(group.sourceCompoundId, group.targetCompoundId), sourceId: group.sourceCompoundId, targetId: group.targetCompoundId, label: group.label, count: group.count, edgeGroupId: group.edgeGroupId, edgeIds: group.edgeIds, edges: [] })
  })
  graph.edges.forEach((edge) => {
    if (!visibleIds.has(edge.sourceCompoundId) || !visibleIds.has(edge.targetCompoundId)) return
    const key = pairKey(edge.sourceCompoundId, edge.targetCompoundId)
    const current = pairMap.get(key)
    const next: PairEntry = current || { key, sourceId: edge.sourceCompoundId, targetId: edge.targetCompoundId, label: edge.card?.primaryName || edge.label, count: 0, edgeIds: [], edges: [] }
    next.count = Math.max(next.count, 1)
    next.edgeIds = Array.from(new Set([...next.edgeIds, edge.edgeId]))
    next.edges = Array.from(new Map([...next.edges, edge].map((item) => [item.edgeId, item])).values())
    next.label = next.label || edge.card?.primaryName || edge.label
    pairMap.set(key, next)
  })
  return [...pairMap.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function buildHomeDegreeScore(graph: HomeGraphData) {
  const score = new Map<string, number>()
  const bump = (id: string, value = 1) => score.set(id, (score.get(id) || 0) + value)
  graph.edges.forEach((edge) => { bump(edge.sourceCompoundId); bump(edge.targetCompoundId) })
  graph.edgeGroups.forEach((group) => { bump(group.sourceCompoundId, group.count); bump(group.targetCompoundId, group.count) })
  return score
}

function pickImportantHomeLabelIds(nodes: NodeCard[]) {
  return new Set(
    [...nodes]
      .sort((a, b) => b.degree - a.degree || a.name.localeCompare(b.name))
      .slice(0, HOME_IMPORTANT_LABEL_COUNT)
      .map((node) => node.compoundId),
  )
}

function createInitialHomePositions(nodes: HomeGraphCompound[], graph: HomeGraphData) {
  const positions: Record<string, Point> = {}
  const velocities: Record<string, Point> = {}
  const center = { x: 50, y: 58 }
  const visibleIds = new Set(nodes.map((node) => node.compoundId))
  const links = buildForceLayoutLinks(graph, visibleIds)
  const degreeScore = buildHomeDegreeScore(graph)
  const total = Math.max(nodes.length, 1)

  nodes.forEach((node, index) => {
    const jitter = stableJitter(node.compoundId)
    const angle = index * HOME_GOLDEN_ANGLE + jitter.x * 0.18
    const radius = 8 + Math.sqrt((index + 0.5) / total) * 50
    positions[node.compoundId] = {
      x: center.x + Math.cos(angle) * radius * 0.86 + jitter.x * 2.5,
      y: center.y + Math.sin(angle) * radius * 0.96 + jitter.y * 2.5,
    }
    velocities[node.compoundId] = { x: 0, y: 0 }
  })

  for (let iteration = 0; iteration < HOME_FORCE_ITERATIONS; iteration += 1) {
    const heat = 1 - iteration / HOME_FORCE_ITERATIONS
    for (let first = 0; first < nodes.length; first += 1) {
      for (let second = first + 1; second < nodes.length; second += 1) {
        const source = nodes[first]
        const target = nodes[second]
        if (!source || !target) continue
        const sourcePoint = positions[source.compoundId]
        const targetPoint = positions[target.compoundId]
        const sourceVelocity = velocities[source.compoundId]
        const targetVelocity = velocities[target.compoundId]
        if (!sourcePoint || !targetPoint || !sourceVelocity || !targetVelocity) continue
        let dx = sourcePoint.x - targetPoint.x
        let dy = sourcePoint.y - targetPoint.y
        if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) {
          const jitter = stableJitter(`${source.compoundId}:${target.compoundId}`)
          dx = jitter.x || 0.1
          dy = jitter.y || 0.1
        }
        const distanceSq = clamp(dx * dx + dy * dy, 12, 2200)
        const distance = Math.sqrt(distanceSq)
        const force = (HOME_FORCE_REPULSION * heat) / distanceSq
        const forceX = (dx / distance) * force
        const forceY = (dy / distance) * force
        sourceVelocity.x += forceX
        sourceVelocity.y += forceY
        targetVelocity.x -= forceX
        targetVelocity.y -= forceY

        const sourceDegree = degreeScore.get(source.compoundId) || 1
        const targetDegree = degreeScore.get(target.compoundId) || 1
        const collisionDistance = HOME_FORCE_COLLISION_DISTANCE + Math.min(2.2, Math.log2(sourceDegree + targetDegree + 1) * 0.28)
        if (distance < collisionDistance) {
          const collisionForce = (collisionDistance - distance) * HOME_FORCE_COLLISION_STRENGTH * (0.45 + heat)
          const collisionX = (dx / distance) * collisionForce
          const collisionY = (dy / distance) * collisionForce
          sourceVelocity.x += collisionX
          sourceVelocity.y += collisionY
          targetVelocity.x -= collisionX
          targetVelocity.y -= collisionY
        }
      }
    }

    links.forEach((link) => {
      const sourcePoint = positions[link.sourceId]
      const targetPoint = positions[link.targetId]
      const sourceVelocity = velocities[link.sourceId]
      const targetVelocity = velocities[link.targetId]
      if (!sourcePoint || !targetPoint || !sourceVelocity || !targetVelocity) return
      const dx = targetPoint.x - sourcePoint.x
      const dy = targetPoint.y - sourcePoint.y
      const distance = Math.max(Math.hypot(dx, dy), 0.001)
      const sourceDegree = degreeScore.get(link.sourceId) || 1
      const targetDegree = degreeScore.get(link.targetId) || 1
      const degreeBoost = Math.min(7, Math.log2(sourceDegree + targetDegree + 1))
      const targetDistance = HOME_FORCE_LINK_DISTANCE + degreeBoost
      const force = (distance - targetDistance) * HOME_FORCE_LINK_STRENGTH * Math.min(2.2, Math.sqrt(link.weight))
      const forceX = (dx / distance) * force
      const forceY = (dy / distance) * force
      sourceVelocity.x += forceX
      sourceVelocity.y += forceY
      targetVelocity.x -= forceX
      targetVelocity.y -= forceY
    })

    nodes.forEach((node) => {
      const point = positions[node.compoundId]
      const velocity = velocities[node.compoundId]
      if (!point || !velocity) return
      velocity.x += (center.x - point.x) * HOME_FORCE_CENTERING
      velocity.y += (center.y - point.y) * HOME_FORCE_CENTERING
      velocity.x *= HOME_FORCE_DAMPING
      velocity.y *= HOME_FORCE_DAMPING
      point.x += velocity.x
      point.y += velocity.y
    })
  }

  return relaxHomePositionCollisions(normalizeHomePositions(positions))
}

function buildForceLayoutLinks(graph: HomeGraphData, visibleIds: Set<string>): ForceLayoutLink[] {
  const links = new Map<string, ForceLayoutLink>()
  const add = (sourceId: string, targetId: string, weight: number) => {
    if (sourceId === targetId || !visibleIds.has(sourceId) || !visibleIds.has(targetId)) return
    const key = canonicalCompoundPairKey(sourceId, targetId)
    const current = links.get(key)
    if (current) {
      current.weight += weight
      return
    }
    links.set(key, { sourceId, targetId, weight })
  }

  graph.edgeGroups.forEach((group) => add(group.sourceCompoundId, group.targetCompoundId, Math.max(1, group.count)))
  graph.edges.forEach((edge) => add(edge.sourceCompoundId, edge.targetCompoundId, 1))
  return [...links.values()]
}

function normalizeHomePositions(positions: Record<string, Point>) {
  const points = Object.values(positions)
  if (points.length === 0) return positions
  const minX = Math.min(...points.map((point) => point.x))
  const maxX = Math.max(...points.map((point) => point.x))
  const minY = Math.min(...points.map((point) => point.y))
  const maxY = Math.max(...points.map((point) => point.y))
  const width = Math.max(maxX - minX, 1)
  const height = Math.max(maxY - minY, 1)
  const scaleX = ((HOME_LAYOUT_WIDTH - HOME_LAYOUT_MARGIN * 2) / width) * 0.96
  const scaleY = ((HOME_LAYOUT_HEIGHT - HOME_LAYOUT_MARGIN * 2) / height) * 0.96
  const sourceCenter = { x: minX + width / 2, y: minY + height / 2 }
  const targetCenter = { x: HOME_VIEWBOX_WIDTH / 2, y: HOME_VIEWBOX_HEIGHT / 2 }
  const normalized: Record<string, Point> = {}
  Object.entries(positions).forEach(([compoundId, point]) => {
    normalized[compoundId] = {
      x: clamp(targetCenter.x + (point.x - sourceCenter.x) * scaleX, HOME_LAYOUT_MIN_X + HOME_LAYOUT_MARGIN, HOME_LAYOUT_MAX_X - HOME_LAYOUT_MARGIN),
      y: clamp(targetCenter.y + (point.y - sourceCenter.y) * scaleY, HOME_LAYOUT_MIN_Y + HOME_LAYOUT_MARGIN, HOME_LAYOUT_MAX_Y - HOME_LAYOUT_MARGIN),
    }
  })
  return normalized
}

function relaxHomePositionCollisions(positions: Record<string, Point>) {
  const next = Object.fromEntries(Object.entries(positions).map(([compoundId, point]) => [compoundId, { ...point }])) as Record<string, Point>
  const entries = Object.entries(next)
  for (let iteration = 0; iteration < HOME_FINAL_COLLISION_ITERATIONS; iteration += 1) {
    let moved = false
    const heat = 1 - iteration / HOME_FINAL_COLLISION_ITERATIONS
    for (let first = 0; first < entries.length; first += 1) {
      for (let second = first + 1; second < entries.length; second += 1) {
        const [sourceId, source] = entries[first] || []
        const [targetId, target] = entries[second] || []
        if (!sourceId || !targetId || !source || !target) continue
        let dx = source.x - target.x
        let dy = source.y - target.y
        let distance = Math.hypot(dx, dy)
        if (distance < 0.001) {
          const jitter = stableJitter(`${sourceId}:${targetId}:final`)
          dx = jitter.x || 0.1
          dy = jitter.y || 0.1
          distance = Math.hypot(dx, dy)
        }
        if (distance >= HOME_FINAL_COLLISION_DISTANCE) continue
        const shift = ((HOME_FINAL_COLLISION_DISTANCE - distance) / 2) * (0.35 + heat * 0.65)
        const shiftX = (dx / distance) * shift
        const shiftY = (dy / distance) * shift
        source.x += shiftX
        source.y += shiftY
        target.x -= shiftX
        target.y -= shiftY
        moved = true
      }
    }
    entries.forEach(([, point]) => {
      point.x = clamp(point.x, HOME_LAYOUT_MIN_X + HOME_LAYOUT_MARGIN, HOME_LAYOUT_MAX_X - HOME_LAYOUT_MARGIN)
      point.y = clamp(point.y, HOME_LAYOUT_MIN_Y + HOME_LAYOUT_MARGIN, HOME_LAYOUT_MAX_Y - HOME_LAYOUT_MARGIN)
    })
    if (!moved) break
  }
  return next
}

function createHomeViewModel(graph: HomeGraphData | null, positions: Record<string, Point>, selectedPairKey: string | null, expandedEdges: HomeGraphEdge[]) {
  if (!graph) return { nodes: [] as NodeCard[], pairs: [] as PairEntry[] }
  const visibleIds = new Set(graph.nodes.map((node) => node.compoundId))
  const pairs = buildHomePairs(graph, visibleIds)
  const score = buildHomeDegreeScore(graph)
  const nodes = [...graph.nodes]
    .sort((a, b) => (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name))
    .map((node) => ({ ...node, degree: score.get(node.compoundId) || 0, x: positions[node.compoundId]?.x ?? 50, y: positions[node.compoundId]?.y ?? 50 }))
  const pairMap = new Map(pairs.map((pair) => [pair.key, pair]))
  if (selectedPairKey && expandedEdges.length > 0) {
    const selectedPair = pairMap.get(selectedPairKey)
    if (selectedPair) pairMap.set(selectedPairKey, { ...selectedPair, edges: expandedEdges, count: Math.max(expandedEdges.length, selectedPair.count) })
  }
  return { nodes, pairs: [...pairMap.values()] }
}

function groupExpandedEdgesByEnzyme(edges: HomeGraphEdge[], referenceSourceId = edges[0]?.sourceCompoundId, referenceTargetId = edges[0]?.targetCompoundId): ExpandedEdgeGroup[] {
  const groups = new Map<string, ExpandedEdgeGroup>()
  edges.forEach((edge) => {
    const enzymeId = edge.card?.enzymeId || edge.enzymeId
    const key = `${canonicalCompoundPairKey(edge.sourceCompoundId, edge.targetCompoundId)}::${enzymeId}`
    const current = groups.get(key)
    if (!current) {
      groups.set(key, {
        key,
        sourceId: referenceSourceId || edge.sourceCompoundId,
        targetId: referenceTargetId || edge.targetCompoundId,
        enzymeId,
        label: edge.card?.uniprotId || edge.card?.databaseCode || edge.enzymeId,
        directionMode: directionModeForEdges([edge], referenceSourceId, referenceTargetId),
        edges: [edge],
        edgeIds: [edge.edgeId],
        reactionIds: [edge.reactionId],
        representative: edge,
      })
      return
    }
    const nextEdges = [...current.edges, edge]
    current.edges = nextEdges
    current.edgeIds = Array.from(new Set([...current.edgeIds, edge.edgeId]))
    current.reactionIds = Array.from(new Set([...current.reactionIds, edge.reactionId]))
    current.directionMode = directionModeForEdges(nextEdges, current.sourceId, current.targetId)
    if (!current.label && (edge.card?.uniprotId || edge.card?.databaseCode || edge.enzymeId)) current.label = edge.card?.uniprotId || edge.card?.databaseCode || edge.enzymeId
  })

  return [...groups.values()].sort((a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key))
}

function canonicalCompoundPairKey(sourceId: string, targetId: string) {
  return [sourceId, targetId].sort().join('::')
}

function directionModeForEdges(edges: HomeGraphEdge[], sourceId = edges[0]?.sourceCompoundId, targetId = edges[0]?.targetCompoundId): ExpandedEdgeGroup['directionMode'] {
  let hasForward = false
  let hasReverse = false
  let hasDirected = false
  edges.forEach((edge) => {
    const normalized = normalizeEdgeDirection(edge.direction)
    const reversedAgainstGroup = edge.sourceCompoundId === targetId && edge.targetCompoundId === sourceId
    if (normalized === 'bidirectional') {
      hasForward = true
      hasReverse = true
      hasDirected = true
      return
    }
    if (normalized === 'forward') {
      if (reversedAgainstGroup) hasReverse = true
      else hasForward = true
      hasDirected = true
      return
    }
    if (normalized === 'reverse') {
      if (reversedAgainstGroup) hasForward = true
      else hasReverse = true
      hasDirected = true
    }
  })
  if (hasForward && hasReverse) return 'bidirectional'
  if (hasForward) return 'forward'
  if (hasReverse) return 'reverse'
  return hasDirected ? 'bidirectional' : 'undirected'
}

function normalizeEdgeDirection(direction: string | null | undefined) {
  const clean = normalizeSearchText(direction)
  if (clean === 'forward') return 'forward'
  if (clean === 'reverse') return 'reverse'
  if (clean === 'reversible' || clean === 'bidirectional' || clean === 'both') return 'bidirectional'
  return 'undirected'
}

function buildHomeSearchSuggestions(query: string, graph: HomeGraphData | null, pairs: PairEntry[], filter: HomeSearchFilter): HomeSearchSuggestion[] {
  if (!graph) return []
  const normalizedQuery = normalizeSearchText(query)
  if (!normalizedQuery) return []
  const suggestions: HomeSearchSuggestion[] = []
  const includeKind = (kind: EntityKind) => filter === 'all' || filter === kind
  const compoundNames = new Map(graph.nodes.map((node) => [node.compoundId, node.name]))

  if (includeKind('compound')) {
    graph.nodes.forEach((node) => {
      const values = [node.name, node.compoundId, node.chebiId, node.formula, node.smiles]
      if (!homeValuesMatch(normalizedQuery, values)) return
      suggestions.push({
        id: `compound:${node.compoundId}`,
        kind: 'compound',
        title: node.name,
        subtitle: node.chebiId || node.compoundId,
        nodeId: node.compoundId,
      })
    })
  }

  pairs.forEach((pair) => {
    const sourceName = compoundNames.get(pair.sourceId) || pair.sourceId
    const targetName = compoundNames.get(pair.targetId) || pair.targetId
    if (includeKind('reaction')) {
      const pairValues = [pair.label, pair.edgeGroupId, pair.sourceId, pair.targetId, sourceName, targetName]
      const representativeEdge = pair.edges[0]
      const edgeValues = representativeEdge
        ? [representativeEdge.reactionId, representativeEdge.card?.reactionEquation, representativeEdge.direction, representativeEdge.sourceType, representativeEdge.reviewStatus]
        : []
      if (homeValuesMatch(normalizedQuery, [...pairValues, ...edgeValues])) {
        suggestions.push({
          id: `reaction:${pair.edgeGroupId || pair.key}`,
          kind: 'reaction',
          title: representativeEdge?.card?.reactionEquation || pair.label,
          subtitle: `${sourceName} -> ${targetName}`,
          pairKey: pair.key,
          reactionId: representativeEdge?.reactionId,
        })
      }
    }

    if (includeKind('enzyme')) {
      pair.edges.forEach((edge) => {
        const values = [
          edge.enzymeId,
          edge.label,
          edge.card?.primaryName,
          edge.card?.uniprotId,
          edge.card?.databaseCode,
          edge.card?.organismName,
          edge.card?.ecNumber,
          edge.reactionId,
          edge.card?.reactionEquation,
        ]
        if (!homeValuesMatch(normalizedQuery, values)) return
        suggestions.push({
          id: `enzyme:${edge.enzymeId}:${edge.edgeId}`,
          kind: 'enzyme',
          title: edge.card?.primaryName || edge.label,
          subtitle: [edge.card?.uniprotId || edge.card?.databaseCode || edge.enzymeId, `${sourceName} -> ${targetName}`].filter(Boolean).join(' · '),
          pairKey: pair.key,
          edgeId: edge.edgeId,
          enzymeId: edge.enzymeId,
          reactionId: edge.reactionId,
        })
      })
    }
  })

  const unique = new Map(suggestions.map((item) => [item.id, item]))
  return [...unique.values()]
    .sort((a, b) => searchSuggestionScore(b, normalizedQuery) - searchSuggestionScore(a, normalizedQuery) || a.title.localeCompare(b.title))
    .slice(0, 8)
}

function homeValuesMatch(normalizedQuery: string, values: Array<string | number | null | undefined>) {
  return values.some((value) => normalizeSearchText(value).includes(normalizedQuery))
}

function searchSuggestionScore(suggestion: HomeSearchSuggestion, normalizedQuery: string) {
  const title = normalizeSearchText(suggestion.title)
  const subtitle = normalizeSearchText(suggestion.subtitle)
  if (title === normalizedQuery) return 100
  if (title.startsWith(normalizedQuery)) return 80
  if (subtitle === normalizedQuery) return 70
  if (subtitle.startsWith(normalizedQuery)) return 50
  return 10
}

function findPairForEndpoints(graph: HomeGraphData, sourceId: string, targetId: string) {
  const visibleIds = new Set(graph.nodes.map((node) => node.compoundId))
  const pairs = buildHomePairs(graph, visibleIds)
  return pairs.find((pair) => pair.sourceId === sourceId && pair.targetId === targetId)
    || pairs.find((pair) => pair.sourceId === targetId && pair.targetId === sourceId)
    || null
}

function pickTargetEdge(edges: HomeGraphEdge[], target?: { edgeId?: string; enzymeId?: string; reactionId?: string }) {
  if (!target) return null
  return edges.find((edge) => target.edgeId && edge.edgeId === target.edgeId)
    || edges.find((edge) => target.enzymeId && edge.enzymeId === target.enzymeId)
    || edges.find((edge) => target.reactionId && edge.reactionId === target.reactionId)
    || null
}

function mergeHomeGraph(base: HomeGraphData | null, addition: HomeGraphData | null): HomeGraphData {
  const seed = base || { nodes: [], edges: [], edgeGroups: [] }
  if (!addition) return seed
  const nodes = new Map(seed.nodes.map((node) => [node.compoundId, node]))
  addition.nodes.forEach((node) => nodes.set(node.compoundId, { ...nodes.get(node.compoundId), ...node }))

  const edges = new Map(seed.edges.map((edge) => [edge.edgeId, edge]))
  addition.edges.forEach((edge) => edges.set(edge.edgeId, { ...edges.get(edge.edgeId), ...edge }))

  const edgeGroups = new Map(seed.edgeGroups.map((group) => [group.edgeGroupId, { ...group, edgeIds: [...group.edgeIds] }]))
  addition.edgeGroups.forEach((group) => {
    const current = edgeGroups.get(group.edgeGroupId)
    if (!current) {
      edgeGroups.set(group.edgeGroupId, { ...group, edgeIds: [...group.edgeIds] })
      return
    }
    const edgeIds = Array.from(new Set([...current.edgeIds, ...group.edgeIds]))
    edgeGroups.set(group.edgeGroupId, { ...current, ...group, edgeIds, count: Math.max(current.count, group.count, edgeIds.length) })
  })

  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    edgeGroups: [...edgeGroups.values()],
  }
}

function addExpansionPositions(current: Record<string, Point>, payload: HomeGraphData, seedId: string, direction: ExpansionDirection) {
  const next = { ...current }
  const seed = next[seedId] || averageHomePosition(next) || { x: HOME_VIEWBOX_WIDTH / 2, y: HOME_VIEWBOX_HEIGHT / 2 }
  const score = buildHomeDegreeScore(payload)
  const incomingNodes = payload.nodes
    .filter((node) => !next[node.compoundId])
    .sort((a, b) => (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name))

  const normal = expansionNormal(direction)
  const tangent = { x: -normal.y, y: normal.x }
  const laneCount = Math.min(9, Math.max(1, incomingNodes.length))

  incomingNodes.forEach((node, index) => {
    const row = Math.floor(index / laneCount)
    const rowStart = row * laneCount
    const rowItems = Math.min(laneCount, incomingNodes.length - rowStart)
    const slot = index - rowStart
    const lateral = (slot - (rowItems - 1) / 2) * 25
    const depth = 48 + row * 42 + Math.abs(slot - (rowItems - 1) / 2) * 2.2
    const jitter = stableJitter(node.compoundId)
    next[node.compoundId] = {
      x: seed.x + normal.x * depth + tangent.x * lateral + jitter.x,
      y: seed.y + normal.y * depth + tangent.y * lateral + jitter.y,
    }
  })

  return next
}

function expansionNormal(direction: ExpansionDirection): Point {
  if (direction === 'left') return { x: -1, y: 0 }
  if (direction === 'right') return { x: 1, y: 0 }
  if (direction === 'top') return { x: 0, y: -1 }
  return { x: 0, y: 1 }
}

function stableJitter(value: string): Point {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) >>> 0
  return {
    x: ((hash % 17) - 8) * 0.12,
    y: (((hash >> 5) % 17) - 8) * 0.12,
  }
}

function averageHomePosition(positions: Record<string, Point>) {
  const points = Object.values(positions)
  if (points.length === 0) return null
  return {
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
  }
}

function getViewportExpansionDirection(positions: Record<string, Point>, camera: Point): ExpansionDirection | null {
  const points = Object.values(positions)
  if (points.length === 0) return null
  const minX = Math.min(...points.map((point) => point.x))
  const maxX = Math.max(...points.map((point) => point.x))
  const minY = Math.min(...points.map((point) => point.y))
  const maxY = Math.max(...points.map((point) => point.y))
  const visible = {
    left: -camera.x,
    right: HOME_VIEWBOX_WIDTH - camera.x,
    top: -camera.y,
    bottom: HOME_VIEWBOX_HEIGHT - camera.y,
  }
  const expansionPadding = 10
  const candidates: Array<{ direction: ExpansionDirection; overflow: number }> = [
    { direction: 'left', overflow: minX - visible.left },
    { direction: 'right', overflow: visible.right - maxX },
    { direction: 'top', overflow: minY - visible.top },
    { direction: 'bottom', overflow: visible.bottom - maxY },
  ]
  const winner = candidates.filter((candidate) => candidate.overflow > expansionPadding).sort((a, b) => b.overflow - a.overflow)[0]
  return winner?.direction ?? null
}

function chooseExpansionSeed(graph: HomeGraphData, positions: Record<string, Point>, direction: ExpansionDirection, attemptedKeys: Set<string> = new Set()) {
  const score = buildHomeDegreeScore(graph)
  const nodes = graph.nodes.filter((node) => positions[node.compoundId] && !attemptedKeys.has(`${direction}:${node.compoundId}`))
  const axis = direction === 'left' || direction === 'right' ? 'x' : 'y'
  const ascending = direction === 'left' || direction === 'top'
  nodes.sort((a, b) => {
    const aPoint = positions[a.compoundId]
    const bPoint = positions[b.compoundId]
    const axisDelta = ascending ? aPoint[axis] - bPoint[axis] : bPoint[axis] - aPoint[axis]
    if (Math.abs(axisDelta) > 0.001) return axisDelta
    return (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name)
  })
  return nodes[0]?.compoundId ?? null
}

function findGraphSearchMatch(query: string, graph: HomeGraphData, pairs: PairEntry[]): GraphSearchMatch {
  const normalizedQuery = normalizeSearchText(query)
  if (!normalizedQuery) return { kind: 'none' }
  const exactNode = graph.nodes.find((node) => [node.compoundId, node.chebiId, node.name].some((value) => normalizeSearchText(value) === normalizedQuery))
  if (exactNode) return { kind: 'node', nodeId: exactNode.compoundId }
  const fuzzyNode = graph.nodes.find((node) => [node.compoundId, node.chebiId, node.name, node.formula, node.smiles].some((value) => normalizeSearchText(value).includes(normalizedQuery)))
  if (fuzzyNode) return { kind: 'node', nodeId: fuzzyNode.compoundId }

  const compoundNames = new Map(graph.nodes.map((node) => [node.compoundId, node.name]))
  for (const pair of pairs) {
    const pairValues = [
      pair.key,
      pair.edgeGroupId,
      pair.label,
      pair.sourceId,
      pair.targetId,
      compoundNames.get(pair.sourceId),
      compoundNames.get(pair.targetId),
    ]
    const edgeMatches = pair.edges.filter((edge) => homeEdgeMatches(edge, normalizedQuery))
    if (edgeMatches.length > 0 || pairValues.some((value) => normalizeSearchText(value).includes(normalizedQuery))) {
      return { kind: 'pair', pair, edges: edgeMatches }
    }
  }
  return { kind: 'none' }
}

function homeEdgeMatches(edge: HomeGraphEdge, normalizedQuery: string) {
  const values = [
    edge.edgeId,
    edge.edgeGroupId,
    edge.reactionId,
    edge.enzymeId,
    edge.label,
    edge.direction,
    edge.sourceType,
    edge.reviewStatus,
    edge.card?.primaryName,
    edge.card?.uniprotId,
    edge.card?.databaseCode,
    edge.card?.organismName,
    edge.card?.ecNumber,
    edge.card?.reactionId,
    edge.card?.reactionEquation,
  ]
  return values.some((value) => normalizeSearchText(value).includes(normalizedQuery))
}

function resolvePathwayEndpoints(query: string, nodes: HomeGraphCompound[]) {
  const separators = [/\s*(?:->|=>|-->|→|到|至)\s*/i, /\s+\bto\b\s+/i, /\s*[，,;；]\s*/]
  for (const separator of separators) {
    const parts = query.split(separator).map((part) => part.trim()).filter(Boolean)
    if (parts.length >= 2) {
      const [startToken, ...endTokens] = parts
      if (!startToken || endTokens.length === 0) continue
      const startId = resolveHomeCompoundToken(startToken, nodes)
      const endId = resolveHomeCompoundToken(endTokens.join(' '), nodes)
      if (startId && endId && startId !== endId) return { startId, endId }
    }
  }
  const idMatches = query.match(/CHEBI:\d+|[A-Z]{2,}[-_:]?\d{2,}/gi) || []
  if (idMatches.length >= 2) {
    const [startToken, endToken] = idMatches
    if (!startToken || !endToken) return null
    const startId = resolveHomeCompoundToken(startToken, nodes)
    const endId = resolveHomeCompoundToken(endToken, nodes)
    if (startId && endId && startId !== endId) return { startId, endId }
  }
  return null
}

function resolveHomeCompoundToken(token: string, nodes: HomeGraphCompound[]) {
  const normalizedToken = normalizeSearchText(token)
  if (!normalizedToken) return null
  const exact = nodes.find((node) => [node.compoundId, node.chebiId, node.name].some((value) => normalizeSearchText(value) === normalizedToken))
  if (exact) return exact.compoundId
  const fuzzy = nodes.find((node) => [node.compoundId, node.chebiId, node.name].some((value) => normalizeSearchText(value).includes(normalizedToken)))
  return fuzzy?.compoundId ?? null
}

function normalizeSearchText(value: string | number | null | undefined) {
  return String(value ?? '').toLowerCase().replace(/\s+/g, ' ').trim()
}

function pairKey(sourceId: string, targetId: string) { return `${sourceId}::${targetId}` }
function edgePath(source: Point, target: Point, offset = 0) {
  const midX = (source.x + target.x) / 2
  const midY = (source.y + target.y) / 2
  const dx = target.x - source.x
  const dy = target.y - source.y
  const length = Math.max(Math.hypot(dx, dy), 0.001)
  const nx = -dy / length
  const ny = dx / length
  return `M ${source.x} ${source.y} Q ${midX + nx * offset} ${midY + ny * offset} ${target.x} ${target.y}`
}
function svgPointerDelta(svg: SVGSVGElement, startClientX: number, startClientY: number, clientX: number, clientY: number) {
  const rect = svg.getBoundingClientRect()
  return {
    x: ((clientX - startClientX) / Math.max(rect.width, 1)) * HOME_VIEWBOX_WIDTH,
    y: ((clientY - startClientY) / Math.max(rect.height, 1)) * HOME_VIEWBOX_HEIGHT,
  }
}
function getNodeExpansionDirection(point: Point, camera: Point): ExpansionDirection | null {
  const viewportPoint = { x: point.x + camera.x, y: point.y + camera.y }
  const margin = 14
  const distances: Array<{ direction: ExpansionDirection; distance: number }> = [
    { direction: 'left', distance: viewportPoint.x },
    { direction: 'right', distance: HOME_VIEWBOX_WIDTH - viewportPoint.x },
    { direction: 'top', distance: viewportPoint.y },
    { direction: 'bottom', distance: HOME_VIEWBOX_HEIGHT - viewportPoint.y },
  ]
  const closest = distances.sort((a, b) => a.distance - b.distance)[0]
  return closest && closest.distance <= margin ? closest.direction : null
}
function clampPanelPosition(point: Point) {
  if (typeof window === 'undefined') return point
  return {
    x: clamp(point.x, 8, Math.max(8, window.innerWidth - 120)),
    y: clamp(point.y, 8, Math.max(8, window.innerHeight - 84)),
  }
}
function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)) }
function wrapCompoundLabel(name: string) {
  const clean = name.replace(/\s+/g, ' ').trim()
  if (!clean) return ['Unknown compound']
  const maxLineLength = 20
  const rows: string[] = []
  let current = ''
  const pushCurrent = () => {
    if (!current.trim()) return
    rows.push(current.trim())
    current = ''
  }
  const appendPart = (part: string) => {
    let rest = part
    while (rest.length > 0) {
      const next = current ? `${current}${rest}` : rest.trimStart()
      if (next.length <= maxLineLength) {
        current = next
        return
      }
      if (current.trim()) {
        pushCurrent()
        continue
      }
      rows.push(rest.slice(0, maxLineLength))
      rest = rest.slice(maxLineLength)
    }
  }

  clean.split(/(\s+|-)/).forEach((part) => {
    if (!part) return
    appendPart(part)
  })
  pushCurrent()
  return rows.length > 0 ? rows : [clean]
}

/* ---------------------------------------------------------------------------
 * Pathway detail sub-view: pure graph slicing + deterministic entity id used by
 * the in-map single-route detail view and its per-step enzyme picker.
 * ------------------------------------------------------------------------- */

/** The one composite group OR the plain edges that back an oriented step
 *  (source→target) in a union graph. A step is either a group (multi-enzyme
 *  pair → its per-enzyme edges live behind loadExpandedEdgeGroup, not in
 *  ``union.edges``) or the plain edges of a single-enzyme pair — never both. */
function resolvePathwayStepPair(union: HomeGraphData, fromId: string, toId: string) {
  const group = union.edgeGroups.find((item) => item.sourceCompoundId === fromId && item.targetCompoundId === toId) ?? null
  const edges = union.edges.filter((edge) => edge.sourceCompoundId === fromId && edge.targetCompoundId === toId)
  return { group, edges }
}

/** Subgraph of ``union`` that paints exactly one returned route: its chain
 *  compounds plus only each adjacent step's own group/single edges. Other
 *  routes' nodes and cross-edges are dropped, so the map shows one pathway. */
function buildPathwayChainGraph(card: HomePathwayCard, union: HomeGraphData): HomeGraphData {
  const keep = new Set(card.compoundIds)
  const nodes = union.nodes.filter((node) => keep.has(node.compoundId))
  const edgeGroups: HomeGraphEdgeGroup[] = []
  const edges: HomeGraphEdge[] = []
  for (let i = 0; i + 1 < card.compoundIds.length; i += 1) {
    const pair = resolvePathwayStepPair(union, card.compoundIds[i], card.compoundIds[i + 1])
    if (pair.group) edgeGroups.push(pair.group)
    pair.edges.forEach((edge) => edges.push(edge))
  }
  return { nodes, edges, edgeGroups }
}

/** Stable 1..stepCount list of {step, source/target id+name, groupId|null, edges}
 *  for the picker + drawer. Names resolve from the chain graph's own nodes. */
function buildPathwayDetailSteps(card: HomePathwayCard, chainGraph: HomeGraphData): PathwayDetailStep[] {
  const names = new Map(chainGraph.nodes.map((node) => [node.compoundId, node.name]))
  const name = (id: string) => names.get(id) ?? id
  const steps: PathwayDetailStep[] = []
  for (let i = 0; i + 1 < card.compoundIds.length; i += 1) {
    const sourceId = card.compoundIds[i]
    const targetId = card.compoundIds[i + 1]
    const pair = resolvePathwayStepPair(chainGraph, sourceId, targetId)
    steps.push({
      step: i + 1,
      sourceId,
      targetId,
      sourceName: name(sourceId),
      targetName: name(targetId),
      groupId: pair.group?.edgeGroupId ?? null,
      edges: pair.edges,
    })
  }
  return steps
}

/** Candidate enzymes backing one step, deduped by the library enzymeId. Name /
 *  organism / sourceType are display copies; ``enzymeId`` is kept verbatim (the
 *  genuine DB primary key) so a future download re-hydrates full records via
 *  POST /search/table/by-ids. ``uniprotId`` (accession) is the popup card's
 *  primary label when the backing edge/card carries one. */
function dedupeEnzymeChoices(edges: HomeGraphEdge[]): PathwayEnzymeChoice[] {
  const seen = new Set<string>()
  const out: PathwayEnzymeChoice[] = []
  edges.forEach((edge) => {
    if (!edge.enzymeId || seen.has(edge.enzymeId)) return
    seen.add(edge.enzymeId)
    const card = edge.card
    out.push({
      enzymeId: edge.enzymeId,
      uniprotId: card?.uniprotId ?? null,
      name: card?.primaryName || edge.label || edge.enzymeId,
      organismName: card?.organismName ?? null,
      sourceType: edge.sourceType ?? null,
    })
  })
  return out
}

/** Deterministic id for an enzyme-picked route so it can coexist with the plain
 *  route under the same (start,end) downloads group. Hash is over the sorted
 *  per-step chosen enzymeIds — stable across StrictMode remounts (no Date.now()). */
function pathwayEnzymeEntityId(cardId: string, perStep: Array<{ step: number; enzymeIds: string[] }>): string {
  const payload = perStep.map((entry) => `${entry.step}:${[...entry.enzymeIds].sort().join(',')}`).join('|')
  let hash = 0x811c9dc5
  for (let i = 0; i < payload.length; i += 1) {
    hash ^= payload.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return `${cardId}#enz-${(hash >>> 0).toString(16)}`
}

/* ---------------------------------------------------------------------------
 * Per-step enzyme picker (right slide-in drawer) for the pathway detail bar's
 * 下载 button. It is NOT a modal: no backdrop, no auto-close from map operation —
 * the map behind stays fully operable. The picker shows ONE current step at a
 * time: a step-bar of chips at the top switches steps, and clicking that step's
 * composite edge on the map does too (bidirectional, driven by the parent's
 * pickerStepIndex). Composite step fan-outs are loaded lazily by the parent
 * (pickerGroupEdges) and shared with the map's expanded-edge rendering. Each
 * candidate card is keyed by the genuine library enzymeId (re-hydration door via
 * POST /search/table/by-ids) and shows the UniProt accession as its primary
 * label when present. Candidates deliberately ignore the map's filter sidebar.
 * ------------------------------------------------------------------------- */
function PathwayEnzymePickerDrawer({
  card,
  steps,
  activeStepIndex,
  onActiveStepChange,
  groupEdges,
  groupLoadingIds,
  onClose,
  onAdd,
  onOpenEnzyme,
}: {
  card: HomePathwayCard
  steps: PathwayDetailStep[]
  activeStepIndex: number
  onActiveStepChange: (index: number) => void
  groupEdges: Record<string, HomeGraphEdge[]>
  groupLoadingIds: string[]
  onClose: () => void
  onAdd: (entity: Entity) => void
  onOpenEnzyme: (enzymeId: string) => void
}) {
  /** enzymeId list per 1-based step number (multi-select, freely revisitable). */
  const [selection, setSelection] = useState<Record<number, string[]>>({})

  const candidatesFor = (step: PathwayDetailStep): PathwayEnzymeChoice[] => {
    if (step.groupId) return dedupeEnzymeChoices(groupEdges[step.groupId] ?? [])
    return dedupeEnzymeChoices(step.edges)
  }
  const isLoading = (step: PathwayDetailStep) =>
    Boolean(step.groupId && groupLoadingIds.includes(step.groupId) && !groupEdges[step.groupId])

  const toggleChoice = (step: number, enzymeId: string) => {
    setSelection((prev) => {
      const current = prev[step] ?? []
      const has = current.includes(enzymeId)
      return { ...prev, [step]: has ? current.filter((id) => id !== enzymeId) : [...current, enzymeId] }
    })
  }

  const chosenStepCount = steps.reduce((count, step) => count + ((selection[step.step]?.length ?? 0) > 0 ? 1 : 0), 0)
  const canAdd = steps.length > 0 && steps.every((step) => (selection[step.step]?.length ?? 0) > 0)

  const handleAdd = () => {
    if (!canAdd) return
    const names = new Map<string, string>()
    steps.forEach((step) => {
      names.set(step.sourceId, step.sourceName)
      names.set(step.targetId, step.targetName)
    })
    const compoundNames = card.compoundIds.map((id) => names.get(id) ?? id)
    const firstId = card.compoundIds[0]
    const lastId = card.compoundIds[card.compoundIds.length - 1]
    const enzymesByStep: PathwayQueueStep[] = steps.map((step) => {
      const chosen = (selection[step.step] ?? [])
        .map((enzymeId) => candidatesFor(step).find((candidate) => candidate.enzymeId === enzymeId))
        .filter((candidate): candidate is PathwayEnzymeChoice => Boolean(candidate))
      return {
        step: step.step,
        sourceId: step.sourceId,
        sourceName: step.sourceName,
        targetId: step.targetId,
        targetName: step.targetName,
        enzymes: chosen,
      }
    })
    const entity: Entity = {
      id: pathwayEnzymeEntityId(card.pathwayId, steps.map((step) => ({ step: step.step, enzymeIds: selection[step.step] ?? [] }))),
      kind: 'pathway',
      name: `${names.get(firstId) ?? firstId} → ${names.get(lastId) ?? lastId}`,
      subtitle: `${card.stepCount} step${card.stepCount === 1 ? '' : 's'} · ${card.compoundIds.length} compound${card.compoundIds.length === 1 ? '' : 's'}`,
      description: card.summary,
      tags: ['Pathway'],
      fields: [],
      related: [],
      pathway: {
        startId: firstId,
        endId: lastId,
        compoundIds: card.compoundIds,
        compoundNames,
        stepCount: card.stepCount,
        enzymesByStep,
      },
    }
    onAdd(entity)
  }

  const activeStep = steps[activeStepIndex]
  const activeLoading = activeStep ? isLoading(activeStep) : false
  const activeCandidates = activeStep && !activeLoading ? candidatesFor(activeStep) : []

  return (
    <aside className="pw-enzyme-drawer" role="dialog" aria-label="为通路每一步选择酶">
      <header className="pw-drawer-header">
        <div>
          <strong>选择每步酶</strong>
          <span>点下方步骤、或图上该步的连线切换 · 可反复改选</span>
        </div>
        <button type="button" className="pw-drawer-close" onClick={onClose} title="关闭" aria-label="关闭选酶面板">
          <X size={18} />
        </button>
      </header>

      <div className="pw-drawer-stepper" role="tablist" aria-label={`通路步骤，共 ${steps.length} 步`}>
        {steps.map((step, index) => {
          const done = (selection[step.step]?.length ?? 0) > 0
          const isActive = index === activeStepIndex
          return (
            <button
              key={step.step}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`pw-drawer-chip${isActive ? ' is-active' : ''}${done ? ' is-done' : ''}`}
              onClick={() => onActiveStepChange(index)}
              title={`第 ${step.step} 步：${step.sourceName} → ${step.targetName}`}
              aria-label={`第 ${step.step} 步，${done ? '已选' : '未选'}`}
            >
              <span className="pw-drawer-chip-tick">{done ? <Check size={12} /> : step.step}</span>
              <span className="pw-drawer-chip-name">第 {step.step} 步</span>
            </button>
          )
        })}
      </div>

      <div className="pw-drawer-body">
        {activeStep && (
          <section className="pw-drawer-step" key={activeStep.step}>
            <h4 className="pw-drawer-step-head">
              <span className="pw-drawer-step-idx">{activeStep.step}</span>
              <span className="pw-drawer-step-names">
                {activeStep.sourceName} <span className="pw-drawer-step-arrow">→</span> {activeStep.targetName}
              </span>
              {(selection[activeStep.step]?.length ?? 0) > 0 && (
                <span className="pw-drawer-step-count">已选 {selection[activeStep.step]?.length}</span>
              )}
            </h4>
            {activeLoading ? (
              <p className="pw-drawer-step-loading">
                <Loader2 size={14} className="pw-drawer-spin" /> 加载该步的酶…
              </p>
            ) : activeCandidates.length === 0 ? (
              <p className="pw-drawer-step-empty">该步暂无酶数据</p>
            ) : (
              <div className="pw-drawer-candidates">
                {activeCandidates.map((enzyme) => {
                  const checked = (selection[activeStep.step] ?? []).includes(enzyme.enzymeId)
                  const entryLabel = enzyme.uniprotId || enzyme.name
                  return (
                    <div key={enzyme.enzymeId} className={`pw-drawer-candidate ${checked ? 'is-checked' : ''}`}>
                      <label className="pw-drawer-candidate-main">
                        <input type="checkbox" checked={checked} onChange={() => toggleChoice(activeStep.step, enzyme.enzymeId)} />
                        <span className="pw-drawer-candidate-copy">
                          <strong className="pw-drawer-candidate-entry">{entryLabel}</strong>
                          {enzyme.uniprotId && enzyme.name && enzyme.name !== enzyme.uniprotId ? (
                            <em className="pw-drawer-candidate-name">{enzyme.name}</em>
                          ) : null}
                          {(enzyme.organismName || enzyme.sourceType) && (
                            <span className="pw-drawer-candidate-sub">
                              {enzyme.organismName ? <span className="pw-drawer-candidate-organism">{enzyme.organismName}</span> : null}
                              {enzyme.sourceType ? <span className="pw-drawer-candidate-src">{enzyme.sourceType}</span> : null}
                            </span>
                          )}
                        </span>
                      </label>
                      <button
                        type="button"
                        className="pw-drawer-candidate-open"
                        onClick={() => onOpenEnzyme(enzyme.enzymeId)}
                        title={`查看 ${entryLabel} 详情`}
                        aria-label={`查看 ${entryLabel} 详情`}
                      >
                        <ArrowUpRight size={13} />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </section>
        )}
      </div>

      <footer className="pw-drawer-footer">
        <span className="pw-drawer-count">
          已选 {chosenStepCount}/{steps.length} 步
        </span>
        <button type="button" className="pw-drawer-add" disabled={!canAdd} onClick={handleAdd}>
          <Check size={15} /> 加入下载表
        </button>
      </footer>
    </aside>
  )
}



















