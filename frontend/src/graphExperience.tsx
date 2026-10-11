import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import {
  ArrowLeft,
  ArrowLeftRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUp,
  CircleHelp,
  Dna,
  Download,
  ExternalLink,
  Layers,
  Link2,
  Loader2,
  Plus,
  Route,
  Search,
  X,
} from 'lucide-react'
import {
  createEnzymeDownload,
  enzymeDetailQueueEntity,
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
  type EnzymeEvidenceDetail,
  type EnzymeSequenceLink,
  type HomeGraphCompound,
  type HomeGraphData,
  type HomeGraphEdge,
  type HomeGraphEdgeGroup,
  type HomeGraphEdgeGroupItem,
  type HomePathwayCard,
} from './api'
import { SearchSetControl } from './components/SearchSetControl'
import { StructureSearchDrawer } from './components/StructureSearchDrawer'
import { fileNameFromUrl, saveFile } from './lib/saveFile'
import { sourceLabel, sourceOptionsFromUnits } from './lib/sourceLabels'
import type { Entity, EntityKind, PathwayEnzymeChoice, PathwayQueueStep } from './types'
import type { MapSearchRoute } from './lib/routes'

const HOME_EXPANSION_LIMIT = 36
const HOME_VIEWBOX_WIDTH = 100
const HOME_VIEWBOX_HEIGHT = 118
const HOME_LAYOUT_BASE_SCALE = 4
const HOME_LAYOUT_MARGIN = 12
const HOME_LAYOUT_REFERENCE_COUNT = 120

/** The box the laid-out map fills, sized to hold `nodeCount` compounds at a constant
 * density.
 *
 * Area is what buys separation: hex packing puts the densest uniform gap at
 * sqrt(area / (n * sqrt(3)/2)), so scaling both sides by sqrt(n / reference) holds
 * that gap steady instead of letting it collapse as the map grows. At the usual ~120
 * compounds the factor is exactly 1, so that map is byte-for-byte what it always was;
 * at the whole ~730-compound graph it is 2.47x.
 *
 * Growing the box does not shrink the drawing. Nothing fits the box to the viewport:
 * the box maps to the screen at a fixed scale (zoomScale 1 by default), so a larger
 * box simply means a larger, pannable canvas. That is already how the map works — the
 * box is 2.8x the viewBox — and it is why the pan and zoom controls earn their place.
 *
 * Measured at 730 compounds against the un-grown box: gaps hold at 13.51 instead of
 * being forced down to 5.67, and edge crossings drop 22518 -> 17025, because the force
 * pass finally has room to untangle instead of packing everything against the clamp.
 *
 * At 300 compounds the base scale is the one lever here that moves crossings
 * MONOTONICALLY, which is why it is the one worth turning. Measured, with every other
 * constant held:
 *
 *   base scale   2.8    3.5    4.0    5.0    6.0    8.0
 *   crossings    174    140    117     93     91     78
 *
 * The same sweep over the force constants is NOT monotone and must not be tuned by
 * eye: at 300 compounds, crossings by HOME_FORCE_ITERATIONS are 174 at 520, 135 at
 * 900, then back up to 178 at 1500, and 183 with damping 0.8. Those are local minima
 * of a chaotic landscape, not improvements — 900 in particular is a lucky draw, so it
 * was left at 520. Compare that with this lever, where six settings in a row move the
 * same way.
 *
 * The price of a bigger box is that fewer compounds fit in one screenful, because the
 * viewBox and the zoom are fixed while the box grows: at 300 compounds the default
 * view holds 40 compounds at 2.8 and 25 at 4.0. That is a real cost and it is the
 * reason this stops at 4.0 rather than continuing down the curve — the user can zoom
 * out (0.6x is allowed) to trade back the other way whenever they want. */
function homeLayoutBoxFor(nodeCount: number) {
  const scale = HOME_LAYOUT_BASE_SCALE * Math.max(1, Math.sqrt(Math.max(nodeCount, 1) / HOME_LAYOUT_REFERENCE_COUNT))
  const width = HOME_VIEWBOX_WIDTH * scale
  const height = HOME_VIEWBOX_HEIGHT * scale
  return {
    width,
    height,
    minX: (HOME_VIEWBOX_WIDTH - width) / 2,
    minY: (HOME_VIEWBOX_HEIGHT - height) / 2,
    maxX: (HOME_VIEWBOX_WIDTH + width) / 2,
    maxY: (HOME_VIEWBOX_HEIGHT + height) / 2,
  }
}
const HOME_IMPORTANT_LABEL_COUNT = 10
const HOME_FORCE_ITERATIONS = 520
const HOME_FORCE_REPULSION = 246
const HOME_FORCE_LINK_DISTANCE = 72
const HOME_FORCE_LINK_STRENGTH = 0.0034
const HOME_FORCE_CENTERING = 0.00028
const HOME_FORCE_DAMPING = 0.68
const HOME_FORCE_COLLISION_DISTANCE = 21.6
const HOME_FORCE_COLLISION_STRENGTH = 0.5

/* How much room a pair of compounds gets, graded by how far apart they are IN THE
 * GRAPH rather than by whether a single line joins them.
 *
 * The earlier cut was binary — "has a line" vs "has none" — and it measured badly:
 * widening the no-line half raised crossings 150 -> 570 at a berth of 24, because
 * 99.4% of pairs have no line, so widening them widens the whole map and drags
 * every real edge along. The graph's own structure says why that was the wrong
 * unit. On the 300-compound map there are 48 connected components: one big one of
 * 170 compounds (56.7%) and 47 islands of 10, 8, 6, 4... 67.6% of all pairs, and
 * 44% of the pairs that sit closer than 20 units, are in DIFFERENT components.
 * Distance tells those two cases apart where "has a line" could not.
 *
 * NO SEPARATION HERE IS FREE. An earlier draft of this comment claimed the
 * cross-component berth cost nothing because no edge spans two islands. That is
 * only half true, and the half it gets wrong is the one that matters: the layout
 * box is a fixed size for a given node count (homeLayoutBoxFor(size) depends on
 * nothing else), so AREA IS CONSERVED. Every unit of separation handed to two
 * compounds is a unit taken from every other pair. Measured, islands at a berth of
 * 24 grow their bounding box 51% (73k -> 111k sq units of a ~197k canvas) and the
 * big component pays for it — so pushing the islands apart still costs crossings
 * even though it stretches no edge.
 *
 * That is why the berths below are FLAT: grading inside a component was implemented
 * and measured, and it loses. Hop-2 pairs 14 -> 17 and hop-3+ 14 -> 20 stretch that
 * component's own real edges (its mean edge length 32.8 -> 40.9, +25%, in a box
 * that did not grow) and it buys 12 fewer label clashes for 37 more crossings.
 * Neighbours also keep the old 14 on their own merits: they are meant to be close,
 * and the line explains why they are.
 *
 * The cross-component berth has to stay near the packing limit for the box — at 300
 * compounds sqrt(area / (n * sqrt(3)/2)) is ~27.5, and past it nothing improves:
 * 30 costs 200 crossings to save 11 clashes. Measured on the live 300-compound map at
 * the old x2.8 base scale:
 *
 *   variant            crossings  label clashes  cross-component pairs under 20
 *   flat 14              150        140           263
 *   islands only, 24     174         86             0
 *   binary cut, 18       210         74           (n/a)
 *   islands + hops, 24   211         74             0
 *
 * Islands-only 24 beats the binary cut on clashes (86 vs 91) and reaches a structural
 * guarantee the binary cut cannot state: no two compounds from different islands are
 * closer than 20 — the nearest such pair in the whole map is 23.86 apart — which is
 * what makes the map read as one dense cluster plus scattered islands.
 *
 * AT THE ADOPTED x4 BASE SCALE THE BERTH NO LONGER BINDS, and that is the point. With
 * room to spare, berth 14 and berth 24 produce byte-identical results (117 crossings,
 * 62 clashes, 0 cross-component pairs under 20) — the islands end up further apart
 * than 24 on their own, so the push never fires and the structural guarantee comes for
 * free instead of being paid for out of the big component's area. That is exactly the
 * trade the comment above warns about, resolved by giving the map more room rather
 * than by tuning the berth: the crossings this berth used to cost at x2.8 (150 -> 174)
 * are gone. It is kept at 24 rather than lowered because at 24 it is demonstrably
 * inert, and it still does its job on any node count where the box is tighter. */
const HOME_HOP_BERTH = [14, 14, 14, 14] as const
const HOME_HOP_CROSS_COMPONENT = 24
/** Marks a pair whose compounds are in different connected components. */
const HOME_HOP_SENTINEL = 255
/* Separation the final pass aims for, as drawn.
 *
 * The box is the 100x118 viewBox at the layout scale, less the 12 margin. The default
 * home map paints 300 compounds with 291 edges (api.ts defaults limit_nodes to 300
 * when there is no centre compound), and hex packing puts the densest uniform gap at
 * sqrt(area / (n * sqrt(3)/2)); at the old x2.8 base that was ~27.5, so both 21.6 and
 * 14 were reachable — measured live, the pass lands on 14.00 exactly, i.e. it
 * converges rather than thrashing. The x4 base raises that limit to ~39, so there is
 * more slack than before, not less. (The figures once quoted here — 120 compounds,
 * 164 edges, 71 crossings — were measured under an older limit_nodes default and no
 * longer describe the home page; only the 120 of HOME_LAYOUT_REFERENCE_COUNT is
 * still 120, and that is the layout reference, not a node count.)
 *
 * The reason to prefer the smaller target is edge length. Asking for 21.6 makes
 * the pass spread the map out to fill the box, which pushes neighbours apart and
 * drags edges across unrelated parts of the graph: measured on the live page,
 * 21.6 gives 129 edge crossings and 5824 units of edge length, versus 71 crossings
 * and 3841 units at 14. Same seed, same node set, same determinism.
 *
 * This target holds at every size because homeLayoutBoxFor grows the box with the
 * compound count: the packing limit stays near 27.5 instead of falling to 11.1 once
 * the whole graph is drawn, so the pass always has room to settle on 14. */
const HOME_FINAL_COLLISION_DISTANCE = 14
const HOME_FINAL_COLLISION_ITERATIONS = 420
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
  /** Model reference score threshold, 0 = no filter. **Applies only to the 'non-membrane'
   *  tier**; membrane proteins are exempt. */
  minScore: number
  /** The 'membrane' tier. A **sibling** of `showNonMembrane`, neither under the other,
   *  both on by default. */
  showMembrane: boolean
  /** The 'non-membrane' tier = `'non-membrane'` **plus** `'unannotated'`. The score
   *  slider is its child: with this tier off the slider has nothing to act on and
   *  greys out. */
  showNonMembrane: boolean
}

/** Membrane-protein marker value. **Only it is exempt from the score threshold** -- it
 *  and the 'non-membrane' tier are peers, and the slider hangs under the latter, so the
 *  slider can never hide a membrane protein. */
const MEMBRANE_VALUE = 'membrane'

/** Model reference score chip. Nothing renders when the score is empty.
 *
 *  Membrane proteins carry `· membrane` in the copy -- what the card needs to explain is
 *  exactly "why was this not filtered out by the threshold", otherwise the user thinks
 *  the filter is broken. `'non-membrane'` / `'unannotated'` get no marker: the former has
 *  only 1,262 rows, so a marker carries no information, and the latter is 80%, so marking
 *  it is just noise. */
function EnzymeScoreChip({ score, membrane }: { score?: number | null; membrane?: string | null }) {
  if (score == null) return null
  const isMembrane = membrane === MEMBRANE_VALUE
  return (
    <span
      className="enzyme-card-score-chip"
      title="DeepSolNet model reference score — not a solubility label"
    >
      DeepSolNet Score {score.toFixed(3)}{isMembrane ? ' · membrane' : ''}
    </span>
  )
}

/** Display copy for the three membrane states. `'unannotated'` is not `'non-membrane'`:
 *  the former is no annotation (80%), the latter is explicitly non-membrane. Both are
 *  bound by the score threshold; only `'membrane'` is exempt, so the exemption line hangs
 *  on membrane alone. */
function membraneLabel(membrane?: string | null) {
  if (membrane === MEMBRANE_VALUE) return 'Membrane — exempt from the score filter'
  if (membrane === 'non-membrane') return 'Non-membrane'
  if (membrane === 'unannotated') return 'Unannotated'
  return 'n/a'
}


function formatScopeEValue(value: number): string {
  return value === 0 ? '0' : value.toExponential(2)
}

type Point = { x: number; y: number }

type PairEntry = {
  /** **Undirected** (`canonicalCompoundPairKey`) -- one row per compound pair. */
  key: string
  /** Endpoints of the dominant direction (chosen deterministically, see
   *  `buildHomePairs`); the arrow and card title follow it. */
  sourceId: string
  targetId: string
  label: string
  count: number
  /** The number of **distinct enzymes** on this pair of endpoints -- that is what the
   *  map's `enzyme*N` line wants. `count` is the record count: when one enzyme
   *  catalyses the same compound pair with two reactions the backend returns two
   *  records, so `count=2` while the enzyme count is 1 (11 of the database's 175 groups
   *  are like this; the largest, FPP↔squalene, is 7093 vs 3595). */
  enzymeCount: number
  /** = `edgeGroupIds[0] ?? null`, kept for call sites that only care about the dominant
   *  direction. */
  edgeGroupId?: string | null
  /** All groups folded in, dominant direction first; `[]` for a non-composite edge. */
  edgeGroupIds: string[]
  edgeIds: string[]
  edges: HomeGraphEdge[]
  /** Endpoints of the folded-away other direction; `null` when this pair has records in
   *  one direction only. */
  reverseSourceId?: string | null
  reverseTargetId?: string | null
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
  // ---- Merge-unit only (does not exist on per-enzyme groups) ----
  // Both render paths (SVG / card stack) read the same set of fields, so making the merge
  // unit **structurally compatible** is far less work than branching the type at every
  // consumption site.
  /** true = this row is a summary of "all enzymes from one source", not a single enzyme. */
  merged?: boolean
  /** The source being summarised (`HomeGraphEdge.sourceType`). */
  sourceType?: string
  /** Number of enzymes summarised (= members.length). */
  memberCount?: number
  /** The per-enzyme groups being summarised, used when the drawer lists them one by one. */
  members?: ExpandedEdgeGroup[]
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
  startedOnBackground: boolean
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
  // Both tiers default to on and the threshold to 0, so "both on + threshold 0" is the
  // default state, not a filter.
  return filters.species.length > 0 || filters.sourceTypes.length > 0
    || filters.minScore > 0 || !filters.showMembrane || !filters.showNonMembrane
}

/** A single sub-edge (composite item or loaded edge) must pass every active dimension. */
function homeUnitPasses(
  unit: {
    organismName?: string | null
    sourceType?: string | null
    deepSolnetScore?: number | null
    membrane?: string | null
  },
  filters: HomeActiveFilters,
) {
  if (filters.species.length > 0 && (!unit.organismName || !filters.species.includes(unit.organismName))) return false
  if (filters.sourceTypes.length > 0 && (!unit.sourceType || !filters.sourceTypes.includes(unit.sourceType))) return false

  // Hierarchy: membrane / non-membrane are peer tiers; the score threshold is a child of
  // "non-membrane" and only constrains it.
  const isMembrane = unit.membrane === MEMBRANE_VALUE
  if (isMembrane) return filters.showMembrane
  if (!filters.showNonMembrane) return false
  // Non-membrane group = 'non-membrane' + 'unannotated'. The threshold reaches only here,
  // never membrane. An enzyme with no score counts as failing -- every enzyme in the
  // database has a score today, but "no data" must not be read as "passes".
  if (filters.minScore > 0) {
    if (unit.deepSolnetScore == null || unit.deepSolnetScore < filters.minScore) return false
  }
  return true
}

function homeEdgePasses(edge: HomeGraphEdge, filters: HomeActiveFilters) {
  return homeUnitPasses({
    organismName: edge.card?.organismName ?? null,
    sourceType: edge.sourceType ?? null,
    deepSolnetScore: edge.card?.deepSolnetScore ?? null,
    membrane: edge.card?.membrane ?? null,
  }, filters)
}

/** Sub-edges that survive the active filters for a collapsed composite pair (group items first, loaded edges fallback).
 *
 * A collapsed pair may carry groups in both directions (one record each way, see
 * `buildHomePairs`), and both must be collected -- taking only `edgeGroupIds[0]` would
 * drop the other direction's enzymes from the panel and the counts. `edges[]` is merged
 * in only when the pair was **actually folded** (`reverseSourceId` non-null): 7
 * single-direction pairs share an edgeId between `edges[]` and their own group, so
 * merging unconditionally would change their `displayCount`. */
function homePairPassingUnits(pair: PairEntry, groupItemMap: Map<string, HomeGraphEdgeGroupItem[]>, filters: HomeActiveFilters): (HomeGraphEdgeGroupItem | HomeGraphEdge)[] {
  const units: (HomeGraphEdgeGroupItem | HomeGraphEdge)[] = []
  const seen = new Set<string>()
  const push = (unit: HomeGraphEdgeGroupItem | HomeGraphEdge) => {
    if (seen.has(unit.edgeId)) return
    seen.add(unit.edgeId)
    units.push(unit)
  }
  pair.edgeGroupIds.forEach((groupId) => { (groupItemMap.get(groupId) ?? []).forEach(push) })
  if (pair.reverseSourceId) pair.edges.forEach(push)
  if (units.length > 0) return units.filter((unit) => homeUnitPasses(unit, filters))
  return pair.edges.filter((edge) => homeEdgePasses(edge, filters))
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

/** One composer slot. ``id`` is the compound the autocomplete resolved (null
 *  while the text is still raw — the server resolves those on Run). */
type PathwayComposerSlot = { id: string | null; text: string }
/** What is *typed* in the composer, as opposed to what is *run*. Kept out of the
 *  URL on purpose: the URL describes the search on screen, this describes the
 *  box. Backing out of a run un-commits the search, and the box keeps the chain
 *  you were describing so you can tweak and re-run it. */
type PathwayDraft = {
  start: PathwayComposerSlot
  end: PathwayComposerSlot
  vias: PathwayComposerSlot[]
}
const emptyPathwayDraft = (): PathwayDraft => ({
  start: { id: null, text: '' },
  end: { id: null, text: '' },
  vias: [],
})

/**
 * `mapSearch` -> a stable comparison key. `undefined` (not on home) must stay distinct
 * from `null` (bare home): the former is "no signal", and treating it as "should clear"
 * would wipe the map's search results every time an enzyme detail page is opened --
 * which is exactly the behaviour this change exists to preserve. NUL-separated because
 * compound names can contain commas.
 */
function mapSearchKey(spec: MapSearchRoute | null | undefined): string {
  if (spec === undefined) return 'off-home'
  if (spec === null) return 'none'
  return [spec.mode, spec.query, spec.start, spec.end, ...spec.via].join('\u0000')
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
  hidden,
  resetNonce,
  onOpenSearch,
  onOpenDownloads,
  onOpenEnzyme,
  onOpenBlast,
  onOpenBlastTable,
  onToggleQueue,
  isQueued,
  queueCount,
  mapSearch,
  mapSearchNonce,
  onMapSearch,
  blastSession,
  autoBlastScope,
  onAutoBlastScopeConsumed,
  onResetHome,
  searchSet,
  onSearchSetChange,
  onQueueMany,
}: {
  /**
   * The map is **resident** for the whole session; leaving the home page only hides it,
   * never unmounts it (see App.tsx). So hide just moves it out of layout and must not
   * touch any internal state -- when the user comes back, everything is as it was.
   */
  hidden?: boolean
  /** App's `resetHome` increment; when it changes, clear this component's own
   *  scope/pathway state. */
  resetNonce?: number
  onOpenSearch: (query?: string) => void
  onOpenDownloads: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onOpenBlast: () => void
  onOpenBlastTable: () => void
  onToggleQueue: (entry: string | Entity) => void
  /** Batch enqueue (the merge drawer's "Queue all"). Calling `onToggleQueue` one by one
   *  is O(n²). */
  onQueueMany: (entries: Entity[]) => void
  isQueued: (id: string) => boolean
  queueCount: number
  /**
   * The search spec on the URL -- a search is **a record in the address bar**, not a
   * piece of this component's internal state. Three states: `undefined` not on home (no
   * signal, do nothing), `null` bare home (clear), a value (run it). See `MapSearchRoute`
   * in `lib/routes.ts`.
   */
  mapSearch?: MapSearchRoute | null
  /** Incremented when the same spec is submitted again -- the only way to notice "the
   *  same word, Enter hit twice". */
  mapSearchNonce?: number
  /** Hands a search to App to write into the URL; this component only consumes the
   *  `mapSearch` that flows back. */
  onMapSearch?: (spec: MapSearchRoute | null) => void
  /** Last completed BLAST run (for scoping the map to its hit enzymes). */
  blastSession?: BlastSession | null
  autoBlastScope?: { sessionId: number; nonce: number } | null
  onAutoBlastScopeConsumed?: () => void
  /** Atlas EDGE brand → drop every active scope/search and head home. */
  onResetHome?: () => void
  /** Search set (search scope, `[]` = all). Graph fetches, suggestions, scope search,
   *  expansion and pathways are all bounded by it. */
  searchSet: string[]
  onSearchSetChange: (next: string[]) => void
}) {
  const [graph, setGraph] = useState<HomeGraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [positions, setPositions] = useState<Record<string, Point>>({})
  const [camera, setCamera] = useState<Point>({ x: 0, y: 0 })
  const [zoomScale, setZoomScale] = useState(1)
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null)
  const [expandedEdges, setExpandedEdges] = useState<HomeGraphEdge[]>([])
  const [expandedLoading, setExpandedLoading] = useState(false)
  const [mapExpanding, setMapExpanding] = useState(false)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  /** The merge unit that was opened (clicking a merge card or merge line) -- the drawer
   *  lists its folded enzymes one by one. Without it those enzymes become "invisible and
   *  undownloadable", which is not acceptable. */
  const [mergedUnit, setMergedUnit] = useState<ExpandedEdgeGroup | null>(null)
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
  /** The left filter panel collapses to give the map the whole width. The panel is
   *  an overlay (the SVG starts at 150px, the panel spans 18..234), so hiding it
   *  only *reveals* graph that was already rendered underneath — the map geometry
   *  is deliberately left alone, which keeps node positions stable across a toggle.
   *  The aside stays mounted and is moved out with transform + visibility rather
   *  than display:none — the latter cannot be transitioned, and unmounting would
   *  drop the filter-menu open/closed state. */
  const [filtersOpen, setFiltersOpen] = useState(true)
  /** The composer unmounts every time it collapses (after a run, or by hand via
   *  the launcher), so its slot text has to live up here — otherwise backing out
   *  of a result reopens the composer blank and the chain just described is gone.
   *  A ref, not state: a keystroke in the composer must not re-render the map. */
  const pathwayDraftRef = useRef<PathwayDraft>(emptyPathwayDraft())
  /** In-map single-route detail sub-view. While non-null the map graph/positions/
   *  camera are swapped to exactly one returned route's chain; ``restore`` is the
   *  union-results view snapshot given back on "Back to results". Never Date.now()-keyed. */
  const [pathwayDetail, setPathwayDetail] = useState<{
    card: HomePathwayCard
    graph: HomeGraphData
    chain: string[]
    restore: { graph: HomeGraphData; positions: Record<string, Point>; camera: Point }
  } | null>(null)
  /** Right slide-in enzyme picker (per-step multi-select) opened by the detail
   *  bar's Download button.While open the map stays fully usable (no modal backdrop):
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
  /** Trace state. While ``traceChain`` is non-null the user is building a
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
  // Both tier chips default to on and the threshold to 0 -- opening shows everything,
  // matching the default view before filters were added.
  const [activeFilters, setActiveFilters] = useState<HomeActiveFilters>({
    species: [], sourceTypes: [], minScore: 0, showMembrane: true, showNonMembrane: true,
  })
  const [speciesOptions, setSpeciesOptions] = useState<string[]>([])
  /** Backend sourceTypes enum; falls back to the built-in 4 values if unreachable. */
  const [sourceTypesFromApi, setSourceTypesFromApi] = useState<string[]>([])
  const [speciesMenuOpen, setSpeciesMenuOpen] = useState(false)
  const [speciesQuery, setSpeciesQuery] = useState('')
  /** The help card for the question mark beside "Solubility score" in the sidebar. It
   *  must dismiss on a background tap / Esc -- the sibling `.home-filter-menu` only
   *  closes by pressing its button again, and a card that covers the slider cannot work
   *  that way. */
  const [scoreHelpOpen, setScoreHelpOpen] = useState(false)
  const scoreHelpRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!scoreHelpOpen) return
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return
      if (event instanceof PointerEvent && scoreHelpRef.current?.contains(event.target as Node)) return
      setScoreHelpOpen(false)
    }
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', dismiss)
    return () => {
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', dismiss)
    }
  }, [scoreHelpOpen])
  const [activeNodeDragId, setActiveNodeDragId] = useState<string | null>(null)
  const [mapDragging, setMapDragging] = useState(false)
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
  /** The pathway card that was selected when the trace began, so Cancel can restore it. */
  const preTraceRef = useRef<{ card: HomePathwayCard | null; id: string | null }>({ card: null, id: null })
  /** Handle for the auto-dismissing trace hint message timer. */
  const traceHintTimerRef = useRef<number | null>(null)
  /** The last URL search actually executed (spec key + nonce), used to decide whether
   *  this one needs to act. */
  const appliedMapSearchRef = useRef<{ key: string; nonce: number } | null>(null)
  const autoBlastHandledRef = useRef<number | null>(null)

  // A changed search set must re-fetch the graph -- the home page's "global browse graph"
  // is itself a search, so it too is within the search set's scope (`selection_mode=global`
  // runs the same edge query). `searchSet` only changes reference when it really changed
  // (App builds a fresh array each time), so this does not re-run needlessly.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    loadHomeGraph({ sourceTypes: searchSet })
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
        // Changing the search set swaps the whole graph, but the scope/session state below
        // **resets the graph, not them**. Clearing them together would leave "browse graph
        // + a still-live pathway session": `scopeActive` true, result cards rendering as
        // usual, but highlight keys pointing at nodes no longer in the graph. Residency
        // makes this path easier to hit (the enzyme detail page also has the search-set
        // control), so it is closed here.
        setPathwayDetail(null)
        setPickerOpen(false)
        setPathwaySession(null)
        setSelectedPathwayId(null)
        setActivePathway(null)
        setPathwayError(null)
        setNoResult(false)
        setNoResultMessage(null)
        setHighlightedNodeIds(new Set())
        setHighlightedEdgeIds(new Set())
        setHighlightedEdgeGroupIds(new Set())
        setScopeSearch(null)
        setEnzymeScopeHitIds(null)
        setBlastScope(null)
        setBlastHitMap(new Map())
        // The snapshot records "the browse graph under the old search set", which no longer
        // matches after a set change, so it must be invalidated.
        browseSnapshotRef.current = null
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
  }, [searchSet])

  useEffect(() => {
    let cancelled = false
    // module=graph: the species dropdown must share the domain of the enzymes that can
    // appear on the map (metadata.py narrows by module), otherwise you get "picked a
    // species, map is empty" -- at the full 96k there are over ten thousand species and
    // only a subset can enter the graph.
    loadMetadataFilters('graph')
      .then((payload) => {
        if (cancelled) return
        setSpeciesOptions((payload.organisms || []).slice())
        setSourceTypesFromApi(payload.sourceTypes || [])
      })
      .catch(() => {
        if (!cancelled) {
          setSpeciesOptions([])
          setSourceTypesFromApi([])
        }
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
  // A line on the map **is a compound pair** (`PairEntry.key` is already undirected), so
  // this must use the undirected key too: otherwise a pathway step running opposite to
  // the dominant direction would not highlight its own line.
  const activePathwayStepKeys = useMemo(() => {
    if (!activePathway) return null
    const chain = activePathway.compoundIds
    if (!chain || chain.length < 2) return null
    const keys = new Set<string>()
    for (let index = 0; index < chain.length - 1; index += 1) {
      keys.add(canonicalCompoundPairKey(chain[index], chain[index + 1]))
    }
    return keys
  }, [activePathway])
  const anyFilterActive = homeFiltersActive(activeFilters)
  // A pathway session pins the map to its union graph: browsing must not expand
  // neighbourhoods into it (drag-to-edge is gated on scopeActive at :522).
  const scopeActive = Boolean(scopeSearch || blastScope || pathwaySession)
  /** True while the in-map single-route detail sub-view is open. Gates every
   *  pathway-mode floating panel (composer/launcher/trace/results list) off. */
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
    // With a search set active, the species dropdown's domain must narrow to those the
    // current scope really draws -- the same principle as the source chips (see
    // sourceOptionsFromUnits): a species in the dropdown that "empties on selection" is
    // exactly what metadata.py's module narrowing guards against, only now narrowed by
    // search set. With an empty search set (the default, all) the old behaviour holds:
    // the API's graph-domain species ∪ those on the current map.
    if (searchSet.length === 0) speciesOptions.forEach((species) => present.add(species))
    groupItemMap.forEach((items) => items.forEach((item) => { if (item.organismName) present.add(item.organismName) }))
    return [...present].sort((a, b) => a.localeCompare(b))
  }, [speciesOptions, groupItemMap, searchSet])
  const pairFilterMeta = useMemo(() => {
    const meta = new Map<string, { visible: boolean; passing: number; enzymes: number; singleLabel: string | null }>()
    if (anyFilterActive) {
      viewModel.pairs.forEach((pair) => {
        const passing = homePairPassingUnits(pair, groupItemMap, activeFilters)
        const first = passing[0]
        meta.set(pair.key, {
          visible: passing.length > 0,
          passing: passing.length,
          // The number of **distinct enzymes** left after filtering -- `passing` is the
          // unit (record) count, and they differ for the same reason as
          // `PairEntry.enzymeCount`.
          enzymes: new Set(passing.map((unit) => unit.enzymeId)).size,
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
  /**
   * The unit list used for rendering -- one more layer of merging **after the existing
   * per-enzyme grouping** (see `mergeAutoAnnotatedUnits`). Only the SVG and the card stack
   * read it; `expandedEdgeGroups` remains the per-enzyme truth, and
   * `selectedExpandedGroup` / `selectedEdge` / filtering / download are unaffected.
   */
  const expandedRenderUnits = useMemo(() => mergeAutoAnnotatedUnits(expandedEdgeGroups), [expandedEdgeGroups])
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

  // Source filter options = the sources **actually present** on the current map (see
  // sourceOptionsFromUnits): the map draws only enzymes with reaction annotations, so a
  // source in the options that is not on the map empties the map the moment it is
  // selected.
  //
  // ⚠️ groupItemMap must be collected too -- collecting only pair.edges is a near-always-
  // true trap: buildHomePairs builds each edgeGroup's pair with `edges: []` (:3652), and
  // only the top-level graph.edges gets filled. The home payload was measured at 157
  // groups / 36,452 items while edges has only 7 -- so seen reflects just those 7 edges'
  // sources. Right now both sources happen to be in there by parity (5 swiss_prot + 2
  // trembl), so the bug is invisible; once edges is 0 (small limit_nodes, or a filter
  // added), present is empty -> sourceOptionsFromUnits falls back to the full API enum,
  // and the options then include ai_literature / manual_literature that are on the map
  // nowhere; selecting one empties the map -- exactly what this function's comment guards
  // against. The species options (:523-528) collect groupItemMap too, and the two are now
  // consistent.
  const sourceFilterOptions = useMemo(() => {
    const seen = new Set<string>()
    groupItemMap.forEach((items) => items.forEach((item) => { if (item.sourceType) seen.add(item.sourceType) }))
    viewModel.pairs.forEach((pair) => {
      pair.edges.forEach((edge) => {
        if (edge.sourceType) seen.add(edge.sourceType)
      })
    })
    // The search set is a hard boundary: a source outside it has not one row in this data,
    // so listing it empties the map on selection -- exactly what the comment above guards
    // against. An empty set does not fall back to the full API enum (that would list enum
    // values with 0 rows).
    return sourceOptionsFromUnits(sourceTypesFromApi, seen, searchSet.length > 0 ? searchSet : null)
  }, [viewModel, groupItemMap, sourceTypesFromApi, searchSet])
  const compoundName = (compoundId: string) => viewModel.nodes.find((node) => node.compoundId === compoundId)?.name || compoundId
  // ---- Trace support data ------------------------------------------------
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
    const addHop = (from: string, to: string) => {
      const targets = map.get(from)
      if (targets) targets.add(to)
      else map.set(from, new Set([to]))
    }
    viewModel.pairs.forEach((pair) => {
      if (anyFilterActive) {
        const meta = pairFilterMeta.get(pair.key)
        if (!meta || !meta.visible) return
      }
      addHop(pair.sourceId, pair.targetId)
      // The folded-away other direction is likewise a real walkable hop on the map (every
      // foldable pair comes from `unknown`, which is two directed edges in the search
      // adjacency anyway). It sits inside the same visibility gate, so a filtered-out pair
      // is still not a legal hop.
      if (pair.reverseSourceId && pair.reverseTargetId) addHop(pair.reverseSourceId, pair.reverseTargetId)
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
      // Look up by the undirected key: a line is a compound pair, and a step running
      // opposite to it lands on the same line. Push **all** edgeGroupIds (not just the
      // dominant direction) -- otherwise a step running opposite to the dominant direction
      // gets `groupId` null, its enzymes are gone, and "download route" loses a step.
      const pair = viewModel.pairs.find((p) => p.key === canonicalCompoundPairKey(from, to))
      const singleId = pair && !pair.edgeGroupId ? pair.edges[0]?.edgeId ?? pair.edgeIds[0] ?? null : null
      const groupId = pair?.edgeGroupId ?? null
      if (singleId) edgeIds.push(singleId)
      if (pair) edgeGroupIds.push(...pair.edgeGroupIds)
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
      searchApiEntries({ q: trimmedSearchValue, pageSize: 8, sourceTypes: searchSet })
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
      const payload = await loadHomeGraph({ centerCompoundId: nodeId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT, sourceTypes: searchSet })
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
    const direction = getNodeExpansionDirection(point, cameraRef.current, zoomScale)
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
      startedOnBackground: event.target instanceof SVGRectElement && event.target.classList.contains('home-map-pan-layer'),
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
    if (Math.abs(deltaX) > 0.8 || Math.abs(deltaY) > 0.8) {
      if (!panState.moved) setMapDragging(true)
      panState.moved = true
    }
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
    setMapDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (!panState.moved && panState.startedOnBackground) clearPairSelection()
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
    const delta = svgPointerDelta(svg, dragState.startClientX, dragState.startClientY, clientX, clientY, zoomScale)
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
    const direction = getNodeExpansionDirection(nextPoint, cameraRef.current, zoomScale)
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
    setHighlightedEdgeGroupIds(new Set(pair.edgeGroupIds.length > 0 ? pair.edgeGroupIds : [pair.key]))
    setSearchFeedback(null)
    focusCameraOnPair(pair)
    let nextEdges: HomeGraphEdge[]
    if (pair.edges.length > 0 && pair.edges.length === pair.count) {
      nextEdges = pair.edges
    } else if (pair.edgeGroupIds.length > 0) {
      setExpandedLoading(true)
      try {
        // A folded pair carries one group per direction; when both sides point at the same
        // records (56 twin pairs) the expansion is identical, so dedupe by edgeId --
        // without deduping the panel counts double.
        const loaded = await Promise.all(pair.edgeGroupIds.map((groupId) => loadExpandedEdgeGroup(groupId, searchSet)))
        const expanded = Array.from(new Map(loaded.flat().map((edge) => [edge.edgeId, edge])).values())
        const edges = pair.reverseSourceId
          ? Array.from(new Map([...pair.edges, ...expanded].map((edge) => [edge.edgeId, edge])).values())
          : expanded
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
    // composite edge out — the "click-edge" half of the bidirectional linkage.
    if (pickerOpen && pathwayDetail) {
      const index = detailSteps.findIndex(
        (step) => canonicalCompoundPairKey(step.sourceId, step.targetId) === pair.key,
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
    // The merge drawer follows the edge group it folded: when the group is gone the drawer
    // must close too.
    setMergedUnit(null)
  }

  /** Open the merge unit's drawer (clicking a merge card or merge line). */
  const openMergedUnit = (unit: ExpandedEdgeGroup) => {
    // The merge line is also "this edge group is selected", matching how clicking a single
    // edge feels.
    setSelectedEdgeId(unit.representative.edgeId)
    setMergedUnit(unit)
  }

  useEffect(() => {
    // This listener is on window, and the map is now resident -- without the guard, a user
    // on an enzyme detail page pressing Esc (say, closing the BLAST drawer) would wipe the
    // **invisible** map selection along the way, and the selected edge/pair would be gone
    // for no reason on return.
    if (hidden) return
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') clearPairSelection()
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [hidden])

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

  const setMinScore = (minScore: number) => {
    setActiveFilters((prev) => ({ ...prev, minScore }))
  }

  /** The two tiers are peers, each with its own switch; neither affects the slider -- the
   *  slider only reads `showNonMembrane`. */
  const toggleMembraneLayer = (layer: 'membrane' | 'nonMembrane') => {
    setActiveFilters((prev) => (layer === 'membrane'
      ? { ...prev, showMembrane: !prev.showMembrane }
      : { ...prev, showNonMembrane: !prev.showNonMembrane }))
  }

  const resetActiveFilters = () => {
    setActiveFilters({
      species: [], sourceTypes: [], minScore: 0, showMembrane: true, showNonMembrane: true,
    })
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

  // ---- Trace interactions ------------------------------------------------
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
  /** Enter trace mode: anchor on the session start compound and let the user click its
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

      const payload = await loadHomeGraph({ centerCompoundId: sourceId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT, sourceTypes: searchSet })
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

  /** Brand click: hand the parent a chance to reset the app-wide search state
   *  and land on home. The map's own scope/search is dropped by the reset-signal
   *  effect below, so all three brand buttons (map, search, downloads) converge
   *  on one path instead of the map one self-clearing first. */
  const handleBrandHome = () => {
    onResetHome?.()
  }

  // `resetHome` used to clean this map by unmounting it. The map is mounted for
  // the whole session now, so the parent has to say so explicitly — otherwise
  // brand-clicking from the search or downloads page returns to a home map still
  // wearing the last scope. The ref skips the initial nonce.
  const resetHandledRef = useRef(resetNonce)
  useEffect(() => {
    if (resetHandledRef.current === resetNonce) return
    resetHandledRef.current = resetNonce
    // The brand button is the only "start over" gesture -- only it clears the draft too.
    // Going back home (spec `null`) is "back out of that search"; the wording differs, so
    // that path keeps the input box.
    pathwayDraftRef.current = emptyPathwayDraft()
    switchSearchMode('enzyme')
    clearSearchScope()
  }, [resetNonce])

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
      const scope = await mapScopeSearch({ q: query, limitNodes: 90, sourceTypes: searchSet })
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
      const scopeGraph = await loadGraphForEnzymes(enzymeIds, { limitNodes: 90, sourceTypes: searchSet })
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

  /** Undo this search on the map and back out its record on the URL too -- otherwise a
   *  refresh revives it. With no App wired up (using this component standalone) it falls
   *  back to local cleanup. */
  const clearSearchEverywhere = () => {
    clearSearchScope()
    if (onMapSearch) onMapSearch(null)
  }

  const handleSearchSubmit = async () => {
    const trimmed = searchValue.trim()
    if (!trimmed) {
      if (scopeActive) clearSearchEverywhere()
      return
    }
    if (resultMode === 'table') {
      clearSearchScope()
      onOpenSearch(trimmed)
      return
    }
    // A search must become a history entry: hand it to App to write into the URL, and the
    // result flows back through `mapSearch`.
    if (onMapSearch) {
      onMapSearch({ mode: 'enzyme', query: trimmed, start: '', end: '', via: [] })
      return
    }
    await runMapEnzymeSearch(trimmed)
  }

  /**
   * The one entry point for search: whatever the address bar says, the map draws.
   *
   * The three states are what makes this work. Opening `/enzymes/X` from `/?q=x` turns
   * the spec to `undefined` (not on home, no signal), so going back to `/?q=x` leaves the
   * spec unchanged and the result still there; one more step back to `/` turns it to
   * `null`, and only then is the map cleared. Merging the two could not express both
   * "opening an enzyme detail page must not clear" and "returning to bare home must
   * clear".
   *
   * `mapSearchNonce` covers only what the URL cannot express: when the same word is
   * entered twice the target URL equals the current address, `navigate` returns early,
   * and the spec alone cannot reveal the second one.
   */
  useEffect(() => {
    if (mapSearch === undefined) return
    const key = mapSearchKey(mapSearch)
    const nonce = mapSearchNonce ?? 0
    if (appliedMapSearchRef.current?.key === key && appliedMapSearchRef.current.nonce === nonce) return
    // Don't consume it while the graph isn't ready -- but do **not** mark it as
    // handled; it still has to run once the graph arrives.
    if (loading || mapExpanding || !graph || graph.nodes.length === 0) return
    appliedMapSearchRef.current = { key, nonce }
    setResultMode('map')
    if (mapSearch === null) {
      clearSearchScope()
      return
    }
    // Pathway mode has no query to run — it opens the composer, which is where
    // a chain is actually described (start / waypoints / end).
    if (mapSearch.mode === 'pathway') {
      if (!mapSearch.start && !mapSearch.end && mapSearch.via.length === 0) {
        // Only `?mode=pathway`: open the composer for describing a new chain. This
        // used to rely on the map being unmounted to yield an empty composer; with the
        // map resident, not clearing it makes returning from an enzyme detail page via
        // "Pathway" show the previous result list plus a collapsed launcher, not the
        // composer for describing a new chain.
        // `clearPathwayResults` opens the composer at its end. The draft is **not**
        // cleared with it: pressing back also lands on this URL (a completed run is
        // `?mode=pathway&start=…`, one step further back is this one), so clearing it
        // would discard the chain just described -- which is exactly what this round fixes.
        clearPathwayResults()
        setSearchMode('pathway')
        return
      }
      // Refill from the URL only when the draft is empty: the composer's `text` is a
      // human-readable compound name, while the URL stores the token handed to the
      // server (`CHEBI:60374`). With a draft, the composer wins, otherwise a good name
      // would be replaced by a token. An empty draft only occurs on deep links /
      // shared links / new tabs.
      const draft = pathwayDraftRef.current
      if (!draft.start.text && !draft.end.text && draft.vias.length === 0) {
        pathwayDraftRef.current = {
          start: { id: null, text: mapSearch.start },
          end: { id: null, text: mapSearch.end },
          vias: mapSearch.via.map((token) => ({ id: null, text: token })),
        }
      }
      // Replay the token verbatim -- the same contract the composer hands the API (the
      // server resolves id / bare ChEBI number / compound name itself), so the
      // `pathwaySession.query` labels come out identical too.
      void runPathwaySearch({
        startCompoundId: mapSearch.start,
        endCompoundId: mapSearch.end,
        viaCompoundIds: mapSearch.via,
      })
      return
    }
    setSearchMode('enzyme')
    void runMapEnzymeSearch(mapSearch.query)
  }, [mapSearch, mapSearchNonce, loading, mapExpanding, graph])

  // Same hand-off for a completed BLAST run: scope the map to the hit enzymes.
  //
  // This hand-off makes App push a bare `/` (`goTo('home')`), so the route effect above
  // sees the spec go from "present" to `null` and calls `clearSearchScope()` -- which
  // looks like it would wipe the BLAST scope just drawn. It doesn't: both effects run in
  // the **same commit** in declaration order, route effect first, this effect second, and
  // its `runBlastScopeSearch` overwrites the scope the former cleared. `setAutoBlastScope`
  // and `goTo('home')` are called in the same event handler (React batching), so they
  // always go together.
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
    // Choosing a returned row is an explicit exit from tracing: stop and highlight
    // the picked card instead of the in-progress chain.
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
   *  snapshot of the union-results view so "Back to results" restores it exactly. */
  const openPathwayDetail = (card: HomePathwayCard) => {
    if (!pathwaySession) return
    // Opening a route detail is an explicit exit from tracing.
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
    const key = canonicalCompoundPairKey(step.sourceId, step.targetId)
    // Highlight the line this step falls on (a folded pair can carry two groups;
    // lighting only the step's own one makes the line look unselected).
    const pair = viewModel.pairs.find((item) => item.key === key)
    setSelectedPairKey(key)
    setSelectedNodeId(null)
    setHighlightedNodeIds(new Set([step.sourceId, step.targetId]))
    setHighlightedEdgeGroupIds(new Set(pair && pair.edgeGroupIds.length > 0 ? pair.edgeGroupIds : [step.groupId || key]))
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
    loadExpandedEdgeGroup(groupId, searchSet)
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
        sourceTypes: searchSet,
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
  const adjustZoom = (next: number) => setZoomScale(Math.min(2.5, Math.max(0.6, next)))
  const handleMapWheel = (event: React.WheelEvent<SVGSVGElement>) => {
    event.preventDefault()
    adjustZoom(zoomScale * (event.deltaY < 0 ? 1.12 : 0.89))
  }

  return (
    <div className={`home-map-page${hidden ? ' map-hidden' : ''}`} style={homeMapStyle}>
      <section className="atlas-map-stage atlas-live-stage" aria-label="Interactive compound graph homepage">
        <header className="graph-top-nav">
          <button type="button" className="atlas-brand" onClick={handleBrandHome} title="Back to the Atlas home map" aria-label="Atlas EDGE home">
            <span className="atlas-logo">
              <img className="atlas-logo-mark" src="/starase-atlas-logo.png" alt="" />
            </span>
            <span>Atlas EDGE</span>
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
            <SearchSetControl value={searchSet} onChange={onSearchSetChange} />
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

        <aside
          className={`graph-filter-sidebar ${filtersOpen ? '' : 'is-collapsed'}`}
          aria-label="Graph filters and controls"
          aria-hidden={!filtersOpen}
        >
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

          {/* With no source to filter, the whole group isn't rendered -- an empty
              chip row is just visual noise. Reached when a search set is active
              but the graph has no source at all. */}
          {sourceFilterOptions.length > 0 && (
          <div className="home-filter-group">
            <p className="home-filter-label">Data source</p>
            <div className="home-chip-row home-source-chips">
              {sourceFilterOptions.map((key) => {
                const checked = activeFilters.sourceTypes.includes(key)
                return (
                  <button key={key} type="button" className={`home-chip ${checked ? 'on' : ''}`} onClick={() => toggleSourceFilter(key)} title={checked ? `Remove ${sourceLabel(key)}` : `Filter by ${sourceLabel(key)}`}>
                    {sourceLabel(key)}
                  </button>
                )
              })}
            </div>
          </div>
          )}

          {/* The two chips are two **peer** levels; the score slider hangs under
              "Non-membrane" and constrains only it. "Non-membrane" includes
              'unannotated' (~20% of the map) -- counting strictly 'non-membrane'
              would leave only 4% of the edges on the map, so the slider would spin
              idle. All 95,869 enzymes in the library have a score, so this slider
              group needs no "has data" guard.

              The group title was changed from "Membrane" to "Solubility score" on
              2026-10-01 (user request), so "title says score, chip below says
              membrane" is not a mistake: membrane is the level **exempt** from the
              score threshold, the two are bound together by design. The help card
              beside it explains this relationship to the user. The slider's own
              label is still DeepSolNet Score, it did not follow the title. */}
          <div className="home-filter-group">
            <div className="home-filter-label-row" ref={scoreHelpRef}>
              <p className="home-filter-label">Solubility score</p>
              <button
                type="button"
                className={`home-filter-help ${scoreHelpOpen ? 'is-open' : ''}`}
                onClick={() => setScoreHelpOpen((open) => !open)}
                aria-expanded={scoreHelpOpen}
                aria-label="What is the solubility score?"
                title="What is the solubility score?"
              >
                <CircleHelp size={13} />
              </button>
              {scoreHelpOpen && (
                <div className="home-filter-help-card" role="note">
                  <p className="home-filter-help-title">Solubility score</p>
                  <p className="home-filter-help-body">
                    Computed by the open-source tool DeepSolNet with its original weights.
                    It is a model reference score, not a strict probability that an enzyme
                    is soluble.
                  </p>
                </div>
              )}
            </div>
            <div className="home-chip-row home-membrane-chips">
              <button
                type="button"
                className={`home-chip ${activeFilters.showMembrane ? 'on' : ''}`}
                onClick={() => toggleMembraneLayer('membrane')}
                title={activeFilters.showMembrane ? 'Hide membrane proteins' : 'Show membrane proteins'}
              >
                {activeFilters.showMembrane ? <Check size={11} /> : null}
                Membrane
              </button>

              {/* "Non-membrane" and its subordinate slider are wrapped together -- the
                  hierarchy is real in the DOM, the slider sits directly under this
                  chip in the layout, not after the whole chip row. */}
              <div className="home-membrane-branch">
                <button
                  type="button"
                  className={`home-chip ${activeFilters.showNonMembrane ? 'on' : ''}`}
                  onClick={() => toggleMembraneLayer('nonMembrane')}
                  title={activeFilters.showNonMembrane ? 'Hide non-membrane proteins' : 'Show non-membrane proteins'}
                >
                  {activeFilters.showNonMembrane ? <Check size={11} /> : null}
                  Non-membrane
                </button>

                <div className={`home-filter-subgroup ${activeFilters.showNonMembrane ? '' : 'is-disabled'}`}>
                  <p
                    className="home-filter-label"
                    title="DeepSolNet model reference score — not a solubility or expression label"
                  >
                    DeepSolNet Score
                  </p>
                  <div className="control-slider-row">
                    <input
                      id="home-min-score"
                      className="control-slider"
                      type="range"
                      min="0"
                      max="1"
                      step="0.01"
                      value={activeFilters.minScore}
                      disabled={!activeFilters.showNonMembrane}
                      onChange={(event) => setMinScore(Number(event.target.value))}
                      aria-label="Minimum DeepSolNet score for non-membrane proteins"
                    />
                    <span className="control-value">{activeFilters.minScore.toFixed(2)}</span>
                  </div>
                  <p className="home-filter-hint">
                    {activeFilters.minScore > 0
                      ? `Hides non-membrane below ${activeFilters.minScore.toFixed(2)}. Membrane proteins exempt.`
                      : 'Non-membrane only. Membrane proteins exempt.'}
                  </p>
                </div>
              </div>
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
                </div>
              </div>
            )}
          </div>

          <button className="graph-filter-link" type="button" onClick={clearPairSelection}>
            Clear selection
          </button>
          <button className="graph-filter-link" type="button" onClick={() => onOpenSearch(searchValue.trim() || undefined)}>
            Open search library
          </button>
          <div className="graph-zoom-controls" aria-label="Map zoom controls">
            <button type="button" onClick={() => adjustZoom(zoomScale - 0.1)} aria-label="Zoom out">−</button>
            <span>{Math.round(zoomScale * 100)}%</span>
            <button type="button" onClick={() => adjustZoom(zoomScale + 0.1)} aria-label="Zoom in">+</button>
            <button type="button" onClick={() => adjustZoom(1)} aria-label="Reset zoom">Reset</button>
          </div>

          <button className="floating-pill download-pill home-pill-button" type="button" onClick={onOpenDownloads}>
            Downloading table
            {queueCount > 0 && <span>{queueCount}</span>}
          </button>
        </aside>

        {/* Both buttons stay mounted and only toggle `is-hidden`, never conditionally
            render -- an unmount disappears in one frame, with nothing to transition.
            `is-hidden` carries visibility:hidden (deferred to the end of the animation),
            so the hidden one is kept out of the tab order. */}
        <button
          className={`home-sidebar-collapse ${filtersOpen ? '' : 'is-hidden'}`}
          type="button"
          onClick={() => setFiltersOpen(false)}
          aria-label="Collapse filters"
          title="Collapse filters"
        >
          <ChevronLeft size={14} />
        </button>

        <button
          className={`home-sidebar-reopen ${filtersOpen ? 'is-hidden' : ''}`}
          type="button"
          onClick={() => setFiltersOpen(true)}
          aria-label="Show filters"
          title="Show filters"
        >
          <ChevronRight size={14} />
          {anyFilterActive && <span className="home-sidebar-reopen-dot" aria-hidden="true" />}
        </button>

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
            <button className="home-scope-action ghost" type="button" onClick={clearSearchEverywhere}>Clear search</button>
          </div>
        )}
        {searchMode === 'enzyme' && blastScope && !blastLoading && (
          <div className="home-scope-feedback blast-scope-feedback">
            <strong className="scope-query">BLASTp</strong>
            <span className="scope-count">
              {blastScope.hits} hit{blastScope.hits === 1 ? '' : 's'} · query {blastScope.queryLength} aa · threshold E-value ≤ {blastScope.threshold === 10 ? '10' : blastScope.threshold.toExponential(0)} · {blastScope.searchedSubjects} subjects
            </span>
            <button className="home-scope-action" type="button" onClick={onOpenBlastTable}>Table results</button>
            <button className="home-scope-action ghost" type="button" onClick={clearSearchEverywhere}>Clear search</button>
          </div>
        )}
        {noResult && !enzymeSearchLoading && (
          <div className="home-noresult-panel" role="status">
            <h3>{searchMode === 'pathway' ? 'No pathway found' : 'Nothing matched on the map'}</h3>
            <p>{noResultMessage || (searchMode === 'pathway' ? 'Try different start/end compounds or fewer intermediate steps.' : 'Try a different compound name, enzyme name, EC number, or organism.')}</p>
            <div className="home-noresult-actions">
              {searchMode === 'pathway' ? (
                <button className="home-scope-action ghost" type="button" onClick={clearSearchEverywhere}>Back to browse map</button>
              ) : (
                <button className="home-scope-action" type="button" onClick={() => onOpenSearch(searchValue.trim() || undefined)}>Search table view</button>
              )}
              <button className="home-scope-action ghost" type="button" onClick={clearSearchEverywhere}>Clear search</button>
            </div>
          </div>
        )}

        {searchMode === 'pathway' && !loading && !error && graph && !detailOpen && (composerOpen ? (
          <PathwaySearchComposer
            busy={pathwaySearchLoading}
            externalError={pathwayError}
            initialDraft={pathwayDraftRef.current}
            onDraftChange={(draft) => {
              pathwayDraftRef.current = draft
            }}
            onRun={(payload) => {
              // As above: write the URL first, then the `mapSearch` round-trip fires
              // `runPathwaySearch`. It carries the same token string the composer hands
              // the API, so replaying from the URL gives byte-identical
              // `pathwaySession.query` labels to running it by hand.
              if (onMapSearch) {
                onMapSearch({
                  mode: 'pathway',
                  query: '',
                  start: payload.startCompoundId,
                  end: payload.endCompoundId,
                  via: payload.viaCompoundIds,
                })
                return
              }
              void runPathwaySearch(payload)
            }}
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

        {/* The trace button floats directly beneath the composer pill (not in the results
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
            <span>Trace</span>
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
            className={`home-map-svg home-live-map ${mapDragging ? 'is-dragging' : ''}`}
            viewBox={`0 0 ${HOME_VIEWBOX_WIDTH} ${HOME_VIEWBOX_HEIGHT}`}
            role="img"
            aria-label="Draggable compound graph"
            onPointerDown={handleMapPointerDown}
            onPointerMove={handleMapPointerMove}
            onPointerUp={finishMapPan}
            onPointerCancel={finishMapPan}
            onWheel={handleMapWheel}
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

            <g className="home-map-camera" transform={`translate(${camera.x + HOME_VIEWBOX_WIDTH / 2} ${camera.y + HOME_VIEWBOX_HEIGHT / 2}) scale(${zoomScale}) translate(${-HOME_VIEWBOX_WIDTH / 2} ${-HOME_VIEWBOX_HEIGHT / 2})`}>
              <g className="home-map-edges live-map-edges">
                {viewModel.pairs.map((pair) => {
                  const source = positions[pair.sourceId]
                  const target = positions[pair.targetId]
                  if (!source || !target) return null
                  const pairMeta = anyFilterActive ? pairFilterMeta.get(pair.key) : undefined
                  if (pairMeta && !pairMeta.visible) return null
                  // A folded pair can carry two groups; highlighting must honour all of them (matches the original single-element logic).
                  const pairGroupIds = pair.edgeGroupIds.length > 0 ? pair.edgeGroupIds : [pair.key]
                  const isExpanded = selectedPairKey === pair.key && pairEdges.length > 0
                  const expandedItems = expandedRenderUnits
                  const offsets = expandedItems.length > 1 ? expandedItems.map((_, index) => (index - (expandedItems.length - 1) / 2) * 5.2) : [0]
                  const displayCount = pairMeta ? pairMeta.passing : pair.count
                  // This label reads `enzyme*N`, where N must be the count of **distinct
                  // enzymes**, not records: when one enzyme catalyses the same compound
                  // pair via two reactions the backend gives two records, `count=2` while
                  // there is only 1 enzyme. Same under filtering -- dedupe by the passing
                  // units. `displayCount` still follows the record count for the line
                  // style (`multi`) and thickness.
                  const labelCount = pairMeta ? pairMeta.enzymes : pair.enzymeCount
                  // When a composite is filtered down to a single surviving enzyme, that enzyme's
                  // accession is the one to annotate the collapsed line with.
                  const filteredSingle = labelCount === 1 && anyFilterActive && pair.edgeGroupIds.length > 0
                    ? homePairPassingUnits(pair, groupItemMap, activeFilters)[0]
                    : undefined
                  const singleUnit = filteredSingle || pair.edges[0]
                  const singleUnitAccession = singleUnit ? homeUnitAccession(singleUnit) : null
                  // With a single enzyme there is no `enzyme*1` form. While a composite
                  // edge is collapsed `pair.edges` may be empty, so it falls through to
                  // `pair.label` -- with one enzyme that is that enzyme's uniprot/name.
                  const singleUnitName = anyFilterActive
                    ? (singleUnit ? homeUnitLabel(singleUnit) : null)
                    : (pair.edges[0]?.card?.primaryName || pair.label)
                  const pairLineLabel = labelCount > 1 ? `enzyme*${labelCount}` : (singleUnitAccession || singleUnitName || 'enzyme')
                  const highlightedPair = pairGroupIds.some((groupId) => highlightedEdgeGroupIds.has(groupId)) || pair.edgeIds.some((edgeId) => highlightedEdgeIds.has(edgeId))
                  const pathwayPair = Boolean(activePathway && activePathwayStepKeys?.has(pair.key))
                  const pairActive = selectedPairKey === pair.key
                  const showPairLabel = pairActive || highlightedPair || pathwayPair
                  const collapsedStroke = (pairActive || highlightedPair || pathwayPair ? 0.42 : 0.28) * edgeThickness
                  // In a scoped result, expanding a composite can surface background
                  // isoenzymes alongside the enzymes that were actually searched. Tag
                  // each expanded line as retrieved (the searched enzymes) or not so
                  // the searched edges can be drawn clearly stronger.
                  const scopedRender = Boolean(scopeHitSet)
                  // A merged unit must test hit against **all its members**: checking only
                  // `group.enzymeId` (= members[0]) would treat the line where "the scoped
                  // search hit the group's 2nd enzyme" as background colour.
                  const retrievedFlags = scopedRender
                    ? expandedItems.map((group) =>
                        group.merged ? Boolean(group.members?.some((member) => scopeHitSet!.has(member.enzymeId))) : scopeHitSet!.has(group.enzymeId),
                      )
                    : null
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
                        // A merged unit's edgeIds are the **union** of all members, so after
                        // expanding, selecting any one member highlights this summary line
                        // too -- expected (it is the line for those edges).
                        const selectedEdgeGroup = edge.edgeIds.includes(selectedEdgeId || '')
                        const retrieved = retrievedFlags ? retrievedFlags[index] : true
                        const isBackground = scopedRender && !retrieved
                        // Retrieved (searched) single edges keep the saturated reaction
                        // hues (rotated among the first four so neighbouring hits stay
                        // distinguishable); background isoenzymes collapse to the plain
                        // `scope-background` tone.
                        //
                        // A merged unit does not take part in the `reaction-color-N`
                        // rotation: it stands for "a whole batch of enzymes from one
                        // source", so the source colour (`.merged-source-line.src-*`) is
                        // the only thing that makes sense, and it gives the user a
                        // distinction from a single Swiss-Prot line at a glance.
                        const reactionColorClass = edge.merged
                          ? `merged-source-line src-${edge.sourceType}`
                          : scopedRender
                            ? (retrieved ? `reaction-color-${retrievedColorOf![index]}` : 'scope-background')
                            : (expandedItems.length === 1 ? 'single-reaction' : `reaction-color-${index % 10}`)
                        const edgeEmphasized = selectedEdgeGroup || highlightedEdge || pathwayEdge
                        const expandedStroke = edge.merged
                          // One line stands for N edges, so one step thicker (same step as "search hit").
                          ? (edgeEmphasized ? 0.72 : 0.6) * edgeThickness
                          : !scopedRender
                            ? (edgeEmphasized ? 0.52 : 0.46) * edgeThickness
                            : retrieved
                              ? (edgeEmphasized ? 0.72 : 0.6) * edgeThickness
                              : (edgeEmphasized ? 0.5 : 0.34) * edgeThickness
                        const openUnit = () => {
                          if (edge.merged) openMergedUnit(edge)
                          else setSelectedEdgeId(edge.representative.edgeId)
                        }
                        return (
                          <g key={edge.key} className={isBackground ? 'scope-miss-edge' : (scopedRender ? 'scope-hit-edge' : undefined)}>
                            <path
                              d={edgePath(source, target, offset)}
                              className={`expanded-edge live-expanded-edge ${reactionColorClass} ${scopedRender && retrieved ? 'scope-retrieved' : ''} ${isBackground ? 'scope-background' : ''} ${selectedEdgeGroup ? 'selected' : ''} ${highlightedEdge ? 'highlighted' : ''} ${pathwayEdge ? 'pathway' : ''}`}
                              style={{ strokeWidth: expandedStroke }}
                              markerStart={edge.directionMode === 'reverse' || edge.directionMode === 'bidirectional' ? 'url(#home-map-arrow)' : undefined}
                              markerEnd={edge.directionMode === 'forward' || edge.directionMode === 'bidirectional' ? 'url(#home-map-arrow)' : undefined}
                              onPointerDown={(event) => event.stopPropagation()}
                              onClick={(event) => { event.stopPropagation(); openUnit() }}
                            />
                            <path d={edgePath(source, target, offset)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); openUnit() }} />
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
            {/* Compounds are not queueable: both Downloading-table pages export
                enzymes, and a compound has no enzyme to export. Compounds still
                appear in a route's exported Markdown diagram. */}
          </div>
        )}

        {selectedPair && !pickerOpen && (
          <div className="enzyme-card-stack live-enzyme-stack map-draggable-panel" style={panelStyle}>
            <div className="stack-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <div>
                <strong>{compoundName(selectedPair.sourceId)} <ChevronRight size={14} /> {compoundName(selectedPair.targetId)}</strong>
                {/* After folding it still reports the **true enzyme count**, followed by
                    a rendered count -- otherwise 3,595 becoming 61 looks like enzymes
                    were lost. Without folding, the second part is not shown. */}
                <span>{expandedLoading ? 'Loading enzyme paths...' : `${expandedEdgeGroups.length} enzymes${expandedRenderUnits.length < expandedEdgeGroups.length ? ` · ${expandedRenderUnits.length} shown` : ''} · ${pairEdges.length || selectedPairTotal} edge${(pairEdges.length || selectedPairTotal) === 1 ? '' : 's'}`}</span>
              </div>
              <button className="stack-close-button" type="button" onClick={clearPairSelection} title="Close enzyme list">
                <X size={18} />
              </button>
            </div>
            {expandedRenderUnits.map((group) => {
              const edge = group.representative
              const enzymeId = edge.card?.enzymeId || edge.enzymeId
              const blastHit = blastHitMap.get(enzymeId)
              const queued = isQueued(enzymeId)
              const queueEntity = homeEnzymeToEntity(edge, enzymeId, compoundName(edge.sourceCompoundId), compoundName(edge.targetCompoundId))
              // Merged card: one card stands for "all enzymes from a source". It is not
              // "a pile of same-named enzymes" -- it may hold dozens of organisms and
              // dozens of ECs, so those two lines report **counts**, not the first
              // member's values.
              if (group.merged) {
                const members = group.members ?? []
                const organisms = new Set<string>()
                const ecNumbers = new Set<string>()
                members.forEach((member) => {
                  const card = member.representative.card
                  if (card?.organismName) organisms.add(card.organismName)
                  if (card?.ecNumber) ecNumbers.add(card.ecNumber)
                })
                const singleMember = members.length === 1 ? members[0] : null
                return (
                  <article
                    key={group.key}
                    className={`enzyme-card enzyme-card-merged src-${group.sourceType} ${group.edgeIds.includes(selectedEdgeId || '') ? 'selected' : ''}`}
                  >
                    {/* Not a Download icon: downloading 3,595 rows is not the action to
                        hint at here. "Queue all" with the number spelled out is at the
                        bottom. */}
                    <button className="card-check" type="button" onClick={() => openMergedUnit(group)} title={`Show all ${members.length.toLocaleString()} entries`}>
                      <Layers size={18} />
                    </button>
                    <button className="enzyme-card-copy" type="button" onClick={() => openMergedUnit(group)}>
                      <h3>{singleMember ? (singleMember.representative.card?.primaryName || singleMember.label) : `${sourceLabel(group.sourceType || '')} entries`}</h3>
                      <span className={`search-table-source-tag ${group.sourceType}`}>{sourceLabel(group.sourceType || '')}</span>
                      {/* Score is shown only for a single member -- a merged card aggregates thousands of rows, so labelling one score misleads. */}
                      {singleMember && <EnzymeScoreChip score={singleMember.representative.card?.deepSolnetScore} membrane={singleMember.representative.card?.membrane} />}
                      <p>{organisms.size} organism{organisms.size === 1 ? '' : 's'}</p>
                      <p>{group.reactionIds.length === 1 ? '1 reaction' : `${group.reactionIds.length} reactions`}</p>
                      {singleMember && <p>{singleMember.representative.card?.organismName || 'Unknown organism'}</p>}
                    </button>
                    <div className="enzyme-card-meta">
                      <strong>{group.label}</strong>
                      <span>{ecNumbers.size === 0 ? 'EC n/a' : `${ecNumbers.size} EC`}</span>
                      <small>{members.length.toLocaleString()} entries</small>
                      <button type="button" onClick={() => openMergedUnit(group)}>Show all</button>
                    </div>
                  </article>
                )
              }
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
                    <EnzymeScoreChip score={edge.card?.deepSolnetScore} membrane={edge.card?.membrane} />
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
                <button className="stack-close-button" type="button" onClick={clearSearchEverywhere} title="Close pathway results and clear the search">
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
                      aria-label={`View route ${index + 1} detail`}
                      title="View this route's detail"
                    >
                      <ArrowUpRight size={13} />
                      <span>Detail</span>
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
          <div className="pw-detail-bar" role="region" aria-label="Route detail">
            <button className="pw-detail-back" type="button" onClick={closePathwayDetail} title="Back to pathway results">
              <ArrowLeft size={14} />
              <span>Back to results</span>
            </button>
            <div className="pw-detail-summary">
              <strong>
                {compoundName(pathwayDetail.chain[0])}
                <span className="pw-detail-summary-arrow"> → </span>
                {compoundName(pathwayDetail.chain[pathwayDetail.chain.length - 1])}
              </strong>
              <span>
                {pathwayDetail.card.stepCount} step{pathwayDetail.card.stepCount === 1 ? '' : 's'} ·{' '}
                {pathwayDetail.chain.length} compound{pathwayDetail.chain.length === 1 ? '' : 's'}
              </span>
            </div>
            <button
              className="pw-detail-download"
              type="button"
              onClick={openPicker}
              disabled={pickerOpen}
              aria-label="Download route"
              title="Pick an enzyme for each step, then add to the downloading table"
            >
              <Download size={14} />
              <span>Download</span>
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
              setSearchFeedback(`Added to the downloading table: ${entity.name}`)
            }}
          />
        )}

        {mergedUnit && (
          <MergedEnzymeDrawer
            unit={mergedUnit}
            compoundLabel={compoundName}
            onClose={() => setMergedUnit(null)}
            onOpenEnzyme={onOpenEnzyme}
            onToggleQueue={(entry) => onToggleQueue(entry)}
            isQueued={isQueued}
            onQueueMany={onQueueMany}
          />
        )}
      </section>
    </div>
  )
}

/* ---------------------------------------------------------------------------
 * Pathway-mode composer: ordered start → (…via…) → end with a whole-library
 * compound dictionary autocomplete on every slot. Rendered only while
 * searchMode === 'pathway'.
 *
 * The slot text is *borrowed* from the parent (`initialDraft`), because this
 * component unmounts every time it collapses — after a run, or by hand via the
 * launcher pill. Owning the text locally meant every remount started blank, so
 * backing out of a result threw away the chain just described. The parent's copy
 * is a ref, so typing here never re-renders the map.
 * ------------------------------------------------------------------------- */
function PathwaySearchComposer({
  busy,
  externalError,
  initialDraft,
  onDraftChange,
  onRun,
  onDismissError,
  onCollapse,
}: {
  busy: boolean
  externalError: string | null
  initialDraft: PathwayDraft
  onDraftChange: (draft: PathwayDraft) => void
  onRun: (payload: PathwayComposerPayload) => void
  onDismissError: () => void
  onCollapse: () => void
}) {
  type ComposerSlot = PathwayComposerSlot
  type ActiveField = { field: 'start' | 'end' | 'via'; viaIndex: number }
  const newSlot = (): ComposerSlot => ({ id: null, text: '' })
  // Seeded once per mount — `initialDraft` is read here and nowhere else, so the
  // composer stays free to edit its own copy while typing.
  const [start, setStart] = useState<ComposerSlot>(() => initialDraft.start)
  const [end, setEnd] = useState<ComposerSlot>(() => initialDraft.end)
  const [vias, setVias] = useState<ComposerSlot[]>(() => initialDraft.vias)
  const [active, setActive] = useState<ActiveField | null>(null)
  const [suggestions, setSuggestions] = useState<CompoundSuggestion[]>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  // Hand every edit up so a remount can pick the composition back up. Writes the
  // parent's ref only — no state there, so typing never re-renders the map.
  useEffect(() => {
    onDraftChange({ start, end, vias })
  }, [start, end, vias, onDraftChange])

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

  /** Swap the two endpoints, `id` included — the resolved compound must travel
   *  with its text, otherwise a swapped slot would keep the old compound's id
   *  and `submit` would send the text and the id of two different compounds. */
  const swapEndpoints = () => {
    setStart(end)
    setEnd(start)
    // The open suggestion dropdown belongs to the slot that used to hold `active`.
    setActive(null)
    setSuggestions([])
    onDismissError()
  }

  const canRun = !busy && start.text.trim().length > 0 && end.text.trim().length > 0
  const canSwap = start.text.trim().length > 0 || end.text.trim().length > 0

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
      {/* Sits next to the End capsule, not in the actions row below: swapping the
          two endpoints belongs to the endpoints, not to Add via / Find pathways. */}
      <button
        className="pw-swap-ends"
        type="button"
        onClick={swapEndpoints}
        disabled={!canSwap}
        title="Swap the start and end compounds"
        aria-label="Swap the start and end compounds"
      >
        <ArrowLeftRight size={14} />
      </button>
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

/** Left-rail modules of the enzyme detail page. Order drives both the rail and
 *  the scroll-spy, so they can never drift apart. */
const ENZYME_MODULES = [
  { id: 'basic', label: 'Basic Information' },
  { id: 'reactions', label: 'Reactions' },
  { id: 'sequence', label: 'Sequence' },
  { id: 'process', label: 'Biological Process' },
  { id: 'xref', label: 'Cross-references' },
  { id: 'references', label: 'References' },
] as const

/** Masses reach us as numbers on the enzyme but as numeric strings on isoforms
 *  ("48114.9"), so normalise before any arithmetic. */
function parseMass(raw: number | string | null | undefined): number | null {
  if (raw === null || raw === undefined || raw === '') return null
  const value = typeof raw === 'number' ? raw : Number.parseFloat(String(raw).replace(/[^0-9.eE+-]/g, ''))
  return Number.isFinite(value) ? value : null
}

/** Signed delta chip text; null when there is nothing to report. */
function formatDelta(value: number, unit: string, digits: number): string | null {
  const threshold = digits === 0 ? 0.5 : 0.5 * Math.pow(10, -digits)
  if (!Number.isFinite(value) || Math.abs(value) < threshold) return null
  const sign = value > 0 ? '+' : '−'
  return `${sign}${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: digits })} ${unit}`
}

/** Sequence comparison ignores the line wrapping the backend uses for display. */
function normalizeSequence(raw: string | null | undefined): string {
  return (raw || '').replace(/\s+/g, '').toUpperCase()
}

export function EnzymeDetailView({ enzymeId, onBack, onToggleQueue, isQueued, queueCount, onOpenDownloads, onOpenBlast, onOpenSearch, onOpenMap, onOpenPathwaySearch, onOpenMapScoped, searchSet, onSearchSetChange }: {
  enzymeId: string | null
  onBack: () => void
  onToggleQueue: (entry: string | Entity) => void
  isQueued: (id: string) => boolean
  queueCount: number
  onOpenDownloads: () => void
  onOpenBlast: () => void
  onOpenSearch: (query: string) => void
  onOpenMap: (query: string) => void
  onOpenPathwaySearch: () => void
  onOpenMapScoped: (query: string) => void
  /** Search set (the search scope, `[]` = all). This page only displays it -- any
   *  search started from here (search box / Map / Pathway) carries it along. */
  searchSet: string[]
  onSearchSetChange: (next: string[]) => void
}) {
  const [detail, setDetail] = useState<EnzymeDetailData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloadState, setDownloadState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [structureOpen, setStructureOpen] = useState(false)
  const [searchDraft, setSearchDraft] = useState('')
  const [activeModule, setActiveModule] = useState<string>(ENZYME_MODULES[0].id)
  /** The element that actually scrolls — the page itself is locked by
   *  `html:has(.home-map-page) { overflow: hidden }`. */
  const scrollRef = useRef<HTMLDivElement | null>(null)

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

  /** A fresh record starts at the top, on its first module. */
  useEffect(() => {
    setActiveModule(ENZYME_MODULES[0].id)
    scrollRef.current?.scrollTo({ top: 0 })
  }, [enzymeId])

  /** Scroll spy: highlight the rail entry of the module the reader is looking
   *  at. Bound to the inner container because the window never scrolls here. */
  useEffect(() => {
    const container = scrollRef.current
    if (!container || !detail) return
    const sync = () => {
      // Bottom fallback: the last module can never reach the container top, so
      // without this it would be unreachable by scrolling.
      if (container.scrollTop + container.clientHeight >= container.scrollHeight - 4) {
        setActiveModule(ENZYME_MODULES[ENZYME_MODULES.length - 1].id)
        return
      }
      const containerTop = container.getBoundingClientRect().top
      let current: string = ENZYME_MODULES[0].id
      for (const module of ENZYME_MODULES) {
        const node = container.querySelector<HTMLElement>(`[data-module="${module.id}"]`)
        if (node && node.getBoundingClientRect().top - containerTop <= 48) current = module.id
      }
      setActiveModule(current)
    }
    sync()
    container.addEventListener('scroll', sync, { passive: true })
    return () => container.removeEventListener('scroll', sync)
  }, [detail])

  const jumpToModule = (id: string) => {
    // Set first so the rail answers the click immediately, before the smooth
    // scroll settles.
    setActiveModule(id)
    const node = scrollRef.current?.querySelector<HTMLElement>(`[data-module="${id}"]`)
    node?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const queued = enzymeId ? isQueued(enzymeId) : false
  const names = detail ? [detail.primaryName, ...detail.secondaryNames].filter(Boolean) : []
  // Some records only carry the canonical sequence on their isoform rows.
  const canonicalSequence = detail?.sequence || detail?.isoforms.find((isoform) => isoform.canonicalSequence)?.canonicalSequence || null
  const sequenceRows = canonicalSequence ? formatSequenceRows(canonicalSequence) : []
  const sequenceLength = detail?.length || canonicalSequence?.length || null
  const sequenceXrefRows = pairSequenceLinks(detail?.sequenceLinks || [])
  const canonicalLengthValue = detail?.length ?? detail?.isoforms.find((isoform) => isoform.canonicalLength)?.canonicalLength ?? null
  const canonicalMassValue = parseMass(detail?.mass) ?? parseMass(detail?.isoforms.find((isoform) => isoform.canonicalMass)?.canonicalMass)
  const isoforms = detail?.isoforms || []
  // UniProt numbers the canonical sequence as isoform -1, so the rows routinely
  // include a variant that *is* the canonical — same residues, same length, and
  // a mass differing only by rounding (48115 vs 48114.9). Listing it as its own
  // block would print the sequence twice under a meaningless "No length
  // difference" chip, so it is folded into the canonical block and merely
  // supplies its isoform id.
  const canonicalKey = normalizeSequence(canonicalSequence)
  const canonicalIsoform = canonicalKey ? isoforms.find((isoform) => normalizeSequence(isoform.sequence) === canonicalKey) || null : null
  const variantIsoforms = canonicalIsoform ? isoforms.filter((isoform) => isoform !== canonicalIsoform) : isoforms

  const handleDownload = async () => {
    if (!detail) return
    setDownloadState('loading')
    try {
      const payload = await createEnzymeDownload(detail.enzymeId, detail.primaryName)
      if (payload.fileUrl) {
        // Not `window.open`: this runs after an await, so the popup is outside
        // the click's user activation and a normal browser blocks it — the
        // button then does nothing at all, with no error to show for it.
        saveFile(payload.fileUrl, fileNameFromUrl(payload.fileUrl))
        setDownloadState('ready')
      } else {
        setDownloadState('error')
      }
    } catch {
      setDownloadState('error')
    }
  }

  return (
    <div className="home-map-page enzyme-atlas-page">
      <section className="enzyme-atlas-stage">
        {/* Same anatomy as the home map / search table top bar. */}
        <header className="graph-top-nav enzyme-topnav">
          {/* `onBack` is a real history pop, so it returns wherever the user came
              from — which may be the search table, not the home map. */}
          <button type="button" className="atlas-brand" onClick={onBack} title="Back" aria-label="Back">
            <span className="atlas-logo">
              <img className="atlas-logo-mark" src="/starase-atlas-logo.png" alt="" />
            </span>
            <span>Atlas EDGE</span>
          </button>

          <div className="enzyme-topnav-slot">
            <SearchSetControl value={searchSet} onChange={onSearchSetChange} />

            <button className="download-list-button" type="button" onClick={onOpenDownloads} title="Open download list">
              <Download size={15} />
              <span>Downloading table</span>
              {queueCount > 0 && <span className="download-list-badge">{queueCount}</span>}
            </button>

            <div className="home-search-bar enzyme-search-bar">
              <div className="home-mode-toggle" role="group" aria-label="Search mode">
                <button type="button" className="is-active" aria-current="page" title="Search compounds and enzymes by keyword / BLAST">Enzyme</button>
                <button type="button" onClick={onOpenPathwaySearch} title="Find compound chains from a start through optional waypoints to an end">Pathway</button>
              </div>
              <input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') onOpenSearch(searchDraft) }}
                placeholder="Search enzymes by name, UniProt, EC or gene…"
                aria-label="Search the enzyme library"
              />
              <div className="home-result-toggle" role="group" aria-label="Search result view">
                <button type="button" onClick={() => onOpenMap(searchDraft)} title="Show these results on the map">Map</button>
                <button type="button" className="is-active" aria-current="page">Table</button>
              </div>
              <button className="home-search-submit" type="button" onClick={() => onOpenSearch(searchDraft)} title="Search">
                <Search size={18} />
              </button>
            </div>
          </div>

          <nav className="graph-primary-nav" aria-label="Enzyme detail navigation">
            <button type="button" onClick={() => onOpenSearch('')}>Data Browser</button>
            <button type="button" onClick={onOpenBlast}>BLAST</button>
            <button type="button" onClick={() => setStructureOpen(true)}>Structure search</button>
            <span className="graph-user-chip">NJU - China 2026</span>
          </nav>
        </header>

        <div className="enzyme-atlas-body">
          <aside className="enzyme-module-rail" aria-label="Enzyme detail sections">
            <div className="enzyme-module-rail-title">Sections</div>
            <nav>
              {ENZYME_MODULES.map((module) => (
                <button
                  key={module.id}
                  type="button"
                  className={activeModule === module.id ? 'is-active' : ''}
                  aria-current={activeModule === module.id ? 'true' : undefined}
                  onClick={() => jumpToModule(module.id)}
                >
                  {module.label}
                </button>
              ))}
            </nav>
          </aside>

          <div className="enzyme-atlas-main" ref={scrollRef}>
            <div className="enzyme-detail-page">
              <section className="detail-atlas-hero">
                <div>
                  <div className="eyebrow"><Dna size={14} /> Enzyme detail</div>
                  <h1>{detail?.primaryName || enzymeId || 'No enzyme selected'}</h1>
                  <p>{detail?.organismName || (loading ? 'Loading detail from the backend...' : '—')}</p>
                </div>
                <div className="detail-hero-actions atlas-detail-actions">
                  <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={15} /> Back</button>
                  {/* The entry is handed over whole rather than as an id: the
                      queue's exportable-kind guard resolves ids through the
                      graph sample, which holds no enzymes, so a bare id was
                      dropped without a word. Disabled until the record lands,
                      like the export button beside it. */}
                  {enzymeId && (
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={!detail}
                      title={detail ? undefined : 'Loading the record'}
                      onClick={() => detail && onToggleQueue(enzymeDetailQueueEntity(detail))}
                    >{queued ? <Check size={15} /> : <Download size={15} />}{queued ? 'Queued' : 'Download'}</button>
                  )}
                  <button className="secondary-button" type="button" onClick={handleDownload} disabled={!detail || downloadState === 'loading'}>{downloadState === 'loading' ? <Loader2 size={15} className="spin" /> : <Download size={15} />} Export record</button>
                </div>
              </section>

              {loading && <div className="detail-status"><Loader2 size={18} className="spin" /> Loading enzyme detail...</div>}
              {error && <div className="detail-status error-state"><X size={18} /> {error}</div>}
              {!enzymeId && !loading && (
                <div className="detail-empty-card"><Dna size={30} /><h2>No enzyme selected</h2><button className="primary-button" type="button" onClick={onBack}><ArrowLeft size={15} /> Back</button></div>
              )}

              {detail && (
                <>
                  {/* 1. Basic information — identity and gene record. The UniProt jump link
                      lives on the card itself; every other external database is
                      already linked from Cross-references, so there is no link card. */}
                  <section className="enzyme-module" data-module="basic" id="enzyme-module-basic">
                    <div className="section-title-row"><h3>Basic Information</h3></div>
                    <div className="detail-card main-detail-card">
                      <div className="detail-card-topline">
                        <span className="detail-chip"><Link2 size={13} /> {detail.databaseCode}</span>
                        {detail.sourceType && (
                          <span className={`search-table-source-tag ${detail.sourceType}`} title="This enzyme's data source">
                            {sourceLabel(detail.sourceType)}
                          </span>
                        )}
                        {detail.uniprotId && <a className="detail-link" href={detail.uniprotUrl || `https://www.uniprot.org/uniprotkb/${detail.uniprotId}`} target="_blank" rel="noreferrer">UniProt {detail.uniprotId} <ExternalLink size={12} /></a>}
                      </div>
                      <div className="detail-name-stack"><h2>{detail.primaryName}</h2><p>{detail.organismName || 'Unknown organism'}</p></div>
                      <div className="tag-row compact">{names.map((name) => <span key={name} className="tag">{name}</span>)}</div>
                      <dl className="detail-facts">
                        <div><dt>Library code</dt><dd>{detail.databaseCode}</dd></div>
                        <div><dt>Species</dt><dd>{detail.organismName || 'n/a'}</dd></div>
                        <div><dt>UniProt</dt><dd>{detail.uniprotId || 'n/a'}</dd></div>
                        <div><dt>Data source</dt><dd>{detail.sourceType ? sourceLabel(detail.sourceType) : 'n/a'}</dd></div>
                        <div><dt>Enzyme review</dt><dd>{detail.reviewStatus || 'n/a'}</dd></div>
                        <div><dt>Gene name</dt><dd>{detail.gene?.geneName || 'n/a'}</dd></div>
                        <div><dt>Length</dt><dd>{sequenceLength ? `${sequenceLength} aa` : 'n/a'}</dd></div>
                        <div><dt>Mass (Da)</dt><dd>{detail.mass ? Math.round(detail.mass).toLocaleString() : 'n/a'}</dd></div>
                        <div><dt>DeepSolNet Score</dt><dd>{detail.deepSolnetScore != null ? detail.deepSolnetScore.toFixed(3) : 'n/a'}</dd></div>
                        <div><dt>Membrane</dt><dd>{membraneLabel(detail.membrane)}</dd></div>
                      </dl>
                    </div>
                  </section>

                  {/* 2. Reaction information */}
                  <section className="enzyme-module" data-module="reactions" id="enzyme-module-reactions">
                    <div className="section-title-row"><h3>Reactions</h3></div>
                    {detail.reactions.length > 0 ? (
                      <div className="reaction-list">
                        {detail.reactions.map((reaction) => (
                          <article key={reaction.reactionId} className="reaction-card">
                            <div className="reaction-card-head">
                              <div><strong>{reaction.equation}</strong><p>{reaction.direction}</p></div>
                              {reaction.rheaUrl ? <a href={reaction.rheaUrl} target="_blank" rel="noreferrer">{reaction.rheaId || 'Rhea'} <ExternalLink size={12} /></a> : <span>{reaction.rheaId || 'Rhea n/a'}</span>}
                            </div>
                            <div className="reaction-meta-grid">
                              <div><span>EC</span><strong>{reaction.ecNumber || 'n/a'}</strong></div>
                              <div className="is-smiles"><span>SMILES</span><strong>{reaction.smiles || 'n/a'}</strong></div>
                              {/* Deliberately NOT "Source type": a reaction is a shared
                                  objective entity, so etl_reactions stamps every reaction row
                                  swiss_prot/official regardless of which enzyme referenced it.
                                  Labelling it plainly made a TrEMBL enzyme look Swiss-Prot —
                                  the enzyme's own source is the "Data source" fact above. */}
                              <div title="The reaction's own source. Reactions are shared between enzymes, so this is always Swiss-Prot even when the enzyme above is TrEMBL.">
                                <span>Reaction source</span><strong>{reaction.sourceType}</strong>
                              </div>
                              <div><span>Reaction review</span><strong>{reaction.reviewStatus}</strong></div>
                            </div>
                            <div className="reaction-compounds">
                              <div><span>Substrates</span><div className="tag-row compact">{reaction.substrates.map((compound) => <CompoundTag key={compound.compoundId} compound={compound} />)}</div></div>
                              <div><span>Products</span><div className="tag-row compact">{reaction.products.map((compound) => <CompoundTag key={compound.compoundId} compound={compound} />)}</div></div>
                            </div>
                            <ReactionAtomMap rheaId={reaction.rheaId} reactionId={reaction.reactionId} />
                          </article>
                        ))}
                      </div>
                    ) : <p className="muted-copy">No reaction records available.</p>}
                  </section>

                  {/* 3. Sequence — canonical first, then one block per isoform */}
                  <section className="enzyme-module" data-module="sequence" id="enzyme-module-sequence">
                    <div className="section-title-row">
                      <h3>Sequence</h3>
                      {canonicalSequence && <button className="small-text-button" type="button" onClick={() => void navigator.clipboard?.writeText(canonicalSequence)}>Copy</button>}
                    </div>

                    {!canonicalSequence && isoforms.length === 0 && <p className="muted-copy">No amino acid sequence available.</p>}

                    {canonicalSequence && (
                      <div className="detail-card enzyme-sequence-block">
                        <div className="enzyme-sequence-head">
                          <strong>Canonical</strong>
                          {canonicalIsoform?.isoformId && <span className="enzyme-sequence-badge is-id">Isoform {canonicalIsoform.isoformId}</span>}
                          <span className="enzyme-sequence-badge">{sequenceLength || canonicalSequence.length} aa</span>
                          {canonicalMassValue != null
                            ? <span className="enzyme-sequence-badge">{Math.round(canonicalMassValue).toLocaleString()} Da</span>
                            : detail.mass ? <span className="enzyme-sequence-badge">{Math.round(detail.mass).toLocaleString()} Da</span> : null}
                        </div>
                        <div className="amino-sequence-view" aria-label="Canonical amino acid sequence">
                          {sequenceRows.map((row) => (
                            <div key={row.start} className="amino-sequence-row">
                              <div className="sequence-ruler"><span />{row.chunks.map((chunk, index) => <span key={`${row.start}:${index}`}>{row.start + index * 10 + chunk.length - 1}</span>)}</div>
                              <div className="sequence-line"><span>{row.start}</span><code>{row.chunks.join(' ')}</code></div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {variantIsoforms.map((isoform, index) => {
                      const isoformRows = isoform.sequence ? formatSequenceRows(isoform.sequence) : []
                      const lengthDelta = isoform.isoformLength != null && canonicalLengthValue != null ? isoform.isoformLength - canonicalLengthValue : null
                      const massDeltaValue = (() => {
                        const isoformMass = parseMass(isoform.isoformMass)
                        return isoformMass != null && canonicalMassValue != null ? isoformMass - canonicalMassValue : null
                      })()
                      const lengthDeltaLabel = lengthDelta === null ? null : formatDelta(lengthDelta, 'aa', 0)
                      const massDeltaLabel = massDeltaValue === null ? null : formatDelta(massDeltaValue, 'Da', 0)
                      const label = isoform.isoformId || `Isoform ${index + 1}`
                      return (
                        <div key={`${label}:${index}`} className="detail-card enzyme-sequence-block enzyme-isoform-block">
                          <div className="enzyme-sequence-head">
                            <strong>Isoform {label}</strong>
                            {/* The variant's own score. Only 30 true variants in the
                                whole library have one -- the rest are null, not
                                rendered. Not repeated on the canonical block; the
                                score there is the enzyme-level one. */}
                            {isoform.deepSolnetScore != null && (
                              <span className="enzyme-sequence-badge" title="DeepSolNet model reference score — not a solubility label">DeepSolNet Score {isoform.deepSolnetScore.toFixed(3)}</span>
                            )}
                            {isoform.isoformLength != null && <span className="enzyme-sequence-badge">{isoform.isoformLength} aa</span>}
                            {isoform.isoformMass && <span className="enzyme-sequence-badge">{Number(parseMass(isoform.isoformMass) || 0).toLocaleString()} Da</span>}
                            <span className={`enzyme-isoform-delta ${lengthDelta === null || lengthDelta === 0 ? 'is-same' : lengthDelta > 0 ? 'is-up' : 'is-down'}`}>
                              {lengthDeltaLabel || 'No length difference'}
                            </span>
                            {massDeltaLabel && (
                              <span className={`enzyme-isoform-delta ${massDeltaValue && massDeltaValue > 0 ? 'is-up' : 'is-down'}`}>{massDeltaLabel}</span>
                            )}
                            {isoform.sequence && <button className="small-text-button" type="button" onClick={() => void navigator.clipboard?.writeText(isoform.sequence || '')}>Copy</button>}
                          </div>
                          {isoformRows.length > 0 ? (
                            <div className="amino-sequence-view" aria-label={`Isoform ${label} amino acid sequence`}>
                              {isoformRows.map((row) => (
                                <div key={row.start} className="amino-sequence-row">
                                  <div className="sequence-ruler"><span />{row.chunks.map((chunk, chunkIndex) => <span key={`${row.start}:${chunkIndex}`}>{row.start + chunkIndex * 10 + chunk.length - 1}</span>)}</div>
                                  <div className="sequence-line"><span>{row.start}</span><code>{row.chunks.join(' ')}</code></div>
                                </div>
                              ))}
                            </div>
                          ) : <p className="muted-copy">No sequence recorded for this isoform.</p>}
                        </div>
                      )
                    })}
                  </section>

                  {/* 4. Biological process — the GO table has no aspect column, and in this
                      dataset every term is a biological process, so the module
                      renders `goTerms` as-is. */}
                  <section className="enzyme-module" data-module="process" id="enzyme-module-process">
                    <div className="section-title-row"><h3>Biological Process</h3></div>
                    {detail.goTerms.length > 0 ? (
                      <div className="enzyme-xref-table is-two-col">
                        <div className="enzyme-xref-head"><span>GO ID</span><span>Biological process</span></div>
                        <div className="enzyme-xref-body">
                          {detail.goTerms.map((term, index) => (
                            <div key={`${term.goId || 'go'}:${index}`} className="enzyme-xref-row">
                              <span className="enzyme-xref-acc">
                                {term.goUrl ? <a href={term.goUrl} target="_blank" rel="noreferrer">{term.goId || 'GO'} <ExternalLink size={12} /></a> : (term.goId || '—')}
                              </span>
                              <span className="enzyme-xref-term">{term.goTerm || '—'}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : <p className="muted-copy">No biological process annotations available.</p>}
                  </section>

                  {/* 5. Sequence identifiers and links — nucleotide paired with its protein */}
                  <section className="enzyme-module" data-module="xref" id="enzyme-module-xref">
                    <div className="section-title-row"><h3>Cross-references</h3></div>
                    {sequenceXrefRows.length > 0 ? (
                      <div className="enzyme-xref-table is-seq-pairs">
                        <div className="enzyme-xref-head"><span>Nucleotide sequence</span><span>Protein sequence</span><span>Molecule type</span></div>
                        <div className="enzyme-xref-body">
                          {sequenceXrefRows.map((row) => (
                            <div key={row.key} className="enzyme-xref-row">
                              <SequenceXrefCellView cell={row.nucleotide} />
                              <SequenceXrefCellView cell={row.protein} />
                              <span className="enzyme-xref-molecule">{row.moleculeType ? row.moleculeType.replace(/_/g, ' ') : '—'}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : <p className="muted-copy">No sequence links available.</p>}
                  </section>

                  {/* 6. References */}
                  <section className="enzyme-module" data-module="references" id="enzyme-module-references">
                    <div className="section-title-row"><h3>References</h3></div>
                    <div className="detail-reference-list">
                      {detail.evidence.length > 0 ? detail.evidence.map((item, index) => {
                        const citation = [item.journal, item.volume, item.pages, item.publicationYear].filter(Boolean).join(' · ')
                        // `url` usually duplicates the PubMed/DOI link — only show it when it is neither.
                        const pubmedUrl = item.pubmedId ? `https://pubmed.ncbi.nlm.nih.gov/${item.pubmedId}/` : null
                        const doiUrl = item.doi ? `https://doi.org/${item.doi}` : null
                        const extraUrl = item.url && item.url !== pubmedUrl && item.url !== doiUrl ? item.url : null
                        // The title is the primary jump target; the buttons stay as explicit labels.
                        const titleUrl = referenceUrl(item)
                        const title = item.title || item.sourceDescription || 'Untitled evidence record'
                        const authors = item.authors ? formatReferenceAuthors(item.authors) : null
                        return (
                          <div key={`${item.doi || item.pubmedId || index}`} className="reference-row enzyme-reference-row">
                            <div className="enzyme-reference-main">
                              {titleUrl
                                ? <a className="enzyme-reference-title is-link" href={titleUrl} target="_blank" rel="noreferrer">{title} <ExternalLink size={11} /></a>
                                : <strong className="enzyme-reference-title">{title}</strong>}
                              {authors && <p className="enzyme-reference-authors" title={authors.truncated ? item.authors || undefined : undefined}>{authors.text}</p>}
                              {citation && <p className="enzyme-reference-citation">{citation}</p>}
                              <div className="enzyme-reference-tags">
                                {item.referenceType && <span className="enzyme-reference-tag">{item.referenceType}</span>}
                                {item.positions && <span className="enzyme-reference-tag is-position">{item.positions}</span>}
                                {item.reviewStatus && <span className="enzyme-reference-tag is-review">{item.reviewStatus}</span>}
                              </div>
                            </div>
                            <div className="reference-links">
                              {extraUrl && <a href={extraUrl} target="_blank" rel="noreferrer">Source</a>}
                              {doiUrl && <a href={doiUrl} target="_blank" rel="noreferrer">DOI</a>}
                              {pubmedUrl && <a href={pubmedUrl} target="_blank" rel="noreferrer">PubMed</a>}
                            </div>
                          </div>
                        )
                      }) : <p className="muted-copy">No evidence links available.</p>}
                    </div>
                  </section>
                </>
              )}
            </div>
          </div>
        </div>
      </section>

      <StructureSearchDrawer
        open={structureOpen}
        onClose={() => setStructureOpen(false)}
        onTransferChebi={(chebiId) => {
          setStructureOpen(false)
          onOpenMapScoped(chebiId)
        }}
      />
    </div>
  )
}

type SequenceRow = {
  start: number
  chunks: string[]
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

/** Authors past this many are abbreviated — the source lists up to 230 names,
 *  which is six lines of text on a card. Records at or below it render in full. */
const REFERENCE_AUTHOR_LIMIT = 10

/** The citation line as a reader expects it: first `REFERENCE_AUTHOR_LIMIT`
 *  names, then `et al.` The source separates names with `;` (verified: no row
 *  uses a bare comma instead). */
function formatReferenceAuthors(authors: string): { text: string; truncated: boolean } {
  const names = authors.split(';').map((name) => name.trim()).filter(Boolean)
  if (names.length <= REFERENCE_AUTHOR_LIMIT) return { text: names.join('; '), truncated: false }
  return { text: `${names.slice(0, REFERENCE_AUTHOR_LIMIT).join('; ')}; et al.`, truncated: true }
}

/** Where the title links to. The DOI is the canonical article identifier, so it
 *  wins; PubMed is the fallback, and `url` (which duplicates PubMed on every
 *  row we have) the last resort. Returns null when the record carries no link. */
function referenceUrl(item: EnzymeEvidenceDetail): string | null {
  if (item.doi) return `https://doi.org/${item.doi}`
  if (item.pubmedId) return `https://pubmed.ncbi.nlm.nih.gov/${item.pubmedId}/`
  return item.url || null
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

/** One side of a cross-reference row: the accession plus a link per database
 *  that carries it (three for INSDC, one for RefSeq). */
type SequenceXrefCell = {
  accession: string
  links: Array<{ source: string; url: string }>
}

/** A nucleotide paired with its protein — the shape of one `ID_n` row in the
 *  source workbook. Either side can be absent. */
type SequenceXrefRow = {
  key: string
  nucleotide: SequenceXrefCell | null
  protein: SequenceXrefCell | null
  moleculeType: string | null
}

/** Database order inside a cell, so the links never reshuffle between renders. */
const XREF_SOURCE_ORDER = ['EMBL', 'GenBank', 'DDBJ', 'RefSeq']

/** The source workbook keeps nucleotide and protein accessions side by side in
 *  one wide row per `ID_n` (`INSDC_Nuc_ID_3` / `INSDC_Prot_ID_3` / …). The ETL
 *  flattens that into one link row per accession and drops `n`, but it mirrors
 *  each accession's partner onto both rows — so the unordered (accession,
 *  partner) pair reconstructs the original row exactly. Verified against the
 *  whole table: no pair has ever carried two different molecule types. */
function pairSequenceLinks(links: EnzymeSequenceLink[]): SequenceXrefRow[] {
  const rows = new Map<string, SequenceXrefRow>()
  links.forEach((link) => {
    const accession = link.accession || ''
    if (!accession) return
    const category = link.category || ''
    const family = /^INSDC/i.test(category) ? 'INSDC' : 'RefSeq'
    const kind = /protein/i.test(category) ? 'protein' : 'nucleotide'
    const partner = link.relatedAccession || ''
    // Both halves of a pair must land on the same key, so normalise the order.
    const key = `${family}|${[accession, partner].sort().join('~')}`
    const row = rows.get(key) || { key, nucleotide: null, protein: null, moleculeType: null }
    rows.set(key, row)
    // Molecule type only rides along as the category's "(mRNA)" suffix, and only
    // INSDC rows have one — RefSeq rows legitimately leave it blank.
    const molecule = category.match(/\(([^)]+)\)\s*$/)?.[1]
    if (molecule) row.moleculeType = molecule
    const cell = row[kind] || { accession, links: [] }
    row[kind] = cell
    if (link.url && !cell.links.some((entry) => entry.url === link.url)) {
      const named = category.match(/\b(EMBL|GenBank|DDBJ)\b/)
      cell.links.push({ source: named ? named[1] : family, url: link.url })
    }
  })
  const ordered = Array.from(rows.values())
  ordered.forEach((row) => {
    for (const cell of [row.nucleotide, row.protein]) {
      cell?.links.sort((a, b) => XREF_SOURCE_ORDER.indexOf(a.source) - XREF_SOURCE_ORDER.indexOf(b.source))
    }
  })
  return ordered
}

/** Rhea draws the atom map for a reaction; we never compute one ourselves, so
 *  the image is fetched through the backend proxy. The proxy figures out which
 *  of the reaction's four Rhea ids actually carries a drawing and answers 404
 *  when none does (polymer reactions have no structure diagram at all). */
/** A substrate/product chip. Every compound in the table carries a ChEBI id, so
 *  these link out; the plain span is only for a row that somehow has neither.
 *  `chebiUrl` is used verbatim — ChEBI does not accept the colon percent-encoded
 *  (`/chebi/CHEBI%3A57310` is a 404), so it must not be passed through `encodeURIComponent`. */
function CompoundTag({ compound }: { compound: HomeGraphCompound }) {
  if (!compound.chebiUrl && !compound.chebiId) return <span className="tag">{compound.name}</span>
  const href = compound.chebiUrl || `https://www.ebi.ac.uk/chebi/searchId.do?chebiId=${compound.chebiId}`
  return (
    <a className="tag is-chebi" href={href} target="_blank" rel="noreferrer" title={`Open ${compound.chebiId || compound.name} in ChEBI`}>
      {compound.name}
      <ExternalLink size={9} />
    </a>
  )
}

function reactionAtomMapUrl(rheaId: string) {
  return `/api/v1/assets/reactions/${encodeURIComponent(rheaId)}/atom-map.svg`
}

/** The atom-mapped reaction drawing, with the attribution Rhea's CC BY 4.0
 *  licence requires. A reaction without a drawing drops the panel entirely
 *  rather than leaving a broken image in the card. */
function ReactionAtomMap({ rheaId, reactionId }: { rheaId?: string | null; reactionId: string }) {
  const [failed, setFailed] = useState(false)
  if (!rheaId || failed) return null
  return (
    <div className="atom-map-wrap">
      <div className="atom-map-head">
        <span>Atom map</span>
        <a href={`https://www.rhea-db.org/rhea/${encodeURIComponent(rheaId.split(':').pop() || rheaId)}`} target="_blank" rel="noreferrer">
          {rheaId} <ExternalLink size={11} />
        </a>
      </div>
      <img
        className="atom-map"
        src={reactionAtomMapUrl(rheaId)}
        alt={`Atom-mapped structure of ${reactionId}`}
        loading="lazy"
        onError={() => setFailed(true)}
      />
      <p className="atom-map-credit">
        Structure and atom numbering from <a href="https://www.rhea-db.org" target="_blank" rel="noreferrer">Rhea</a> (CC BY 4.0, SIB Swiss Institute of Bioinformatics).
      </p>
    </div>
  )
}

/** One cell of the cross-reference table: the accession with a link per database
 *  holding it. A missing side renders as an em dash instead of collapsing the
 *  row, so the pairing stays legible (a genomic segment with no protein partner
 *  is a real shape in this dataset, not a gap to hide). */
function SequenceXrefCellView({ cell }: { cell: SequenceXrefCell | null }) {
  if (!cell) return <span className="enzyme-xref-none">—</span>
  return (
    <span className="enzyme-xref-cell">
      <span className="enzyme-xref-acc">{cell.accession}</span>
      {cell.links.length > 0 && (
        <span className="enzyme-xref-sources">
          {cell.links.map((link) => (
            <a key={link.url} href={link.url} target="_blank" rel="noreferrer" title={`${link.source}: ${cell.accession}`}>
              {link.source} <ExternalLink size={11} />
            </a>
          ))}
        </span>
      )}
    </span>
  )
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

/** One **directed** (source → target) bucket, before the two directions are folded. */
type DirectedPairBucket = {
  sourceId: string
  targetId: string
  label: string
  count: number
  /** The number of **distinct enzymes** on this pair of endpoints (see `PairEntry.enzymeCount`). */
  enzymeCount: number
  group: HomeGraphEdgeGroup | null
  edgeIds: string[]
  edges: HomeGraphEdge[]
  /** Whether the payload's `edgeIds`/`items` fully cover `count` (see `buildHomePairs`). */
  complete: boolean
}

/**
 * Home edge list: **one entry per compound pair**.
 *
 * Why fold: `graph_service._build_edges_and_groups` groups by **directed** pair, and
 * `unknown` belongs to both direction whitelists (`graph_service.py:17-18`), so one
 * `unknown` reaction gets a record generated for each direction, **sharing the same
 * `edge_id`**. The frontend used to index by directed key, so the same compound pair
 * was drawn as two exactly overlapping lines with two labels stacked on one coordinate
 * -- measured, 80 of 371 lines were this kind of duplicate, the main cause of home-map
 * labels occluding each other (adjusting node spacing can never separate same-coordinate
 * labels).
 *
 * Folding **loses no direction information**: anything foldable must be an `unknown`
 * pair (both directions legal, and the search adjacency already holds two directed
 * edges), while `forward`/`reverse` pairs are single-direction pairs (a reverse record
 * is never generated), each its own bucket, arrows unchanged. The layout layer
 * `buildForceLayoutLinks` was undirected all along, so only after folding do "what is
 * drawn", "what the layout computes" and "what the search walks" truly agree.
 *
 * Counting **never inflates**: for 56/58 of the twin group pairs the `edgeIds` sets are
 * identical (both sides point at the same records), so adding them would double. That is
 * why the union is taken only when the payload can prove completeness, otherwise the
 * dominant direction's original value is kept.
 */
function buildHomePairs(graph: HomeGraphData, visibleIds: Set<string>) {
  // ---- Pass 1: bucket by **directed** pair. Value precedence matches the pre-fold
  // per-record order, so single-direction pairs (the vast majority) come out
  // byte-identical. ----
  const buckets = new Map<string, DirectedPairBucket>()
  const bucketFor = (sourceId: string, targetId: string) => {
    const key = pairKey(sourceId, targetId)
    const found = buckets.get(key)
    if (found) return found
    const created: DirectedPairBucket = { sourceId, targetId, label: '', count: 0, enzymeCount: 0, group: null, edgeIds: [], edges: [], complete: true }
    buckets.set(key, created)
    return created
  }
  graph.edgeGroups.forEach((group) => {
    if (!visibleIds.has(group.sourceCompoundId) || !visibleIds.has(group.targetCompoundId)) return
    const bucket = bucketFor(group.sourceCompoundId, group.targetCompoundId)
    bucket.group = group
    bucket.label = bucket.label || group.label
    bucket.count = Math.max(bucket.count, group.count)
    bucket.enzymeCount = Math.max(bucket.enzymeCount, group.enzymeCount)
    bucket.edgeIds = Array.from(new Set([...bucket.edgeIds, ...group.edgeIds]))
    // Backend `count` = the number of records grouped, `edgeIds` is the same length;
    // `graph_service.py:180-181` keeps only those whose edge_id also appears in `edges[]`
    // (falling back to the full list when none survive). So "length >= count" is the
    // test for "the payload wasn't truncated" -- a truncated group is short, and the
    // union is refused accordingly.
    bucket.complete = bucket.complete && bucket.edgeIds.length >= group.count
  })
  graph.edges.forEach((edge) => {
    if (!visibleIds.has(edge.sourceCompoundId) || !visibleIds.has(edge.targetCompoundId)) return
    const bucket = bucketFor(edge.sourceCompoundId, edge.targetCompoundId)
    bucket.count = Math.max(bucket.count, 1)
    bucket.enzymeCount = Math.max(bucket.enzymeCount, 1)
    bucket.edgeIds = Array.from(new Set([...bucket.edgeIds, edge.edgeId]))
    bucket.edges = Array.from(new Map([...bucket.edges, edge].map((item) => [item.edgeId, item])).values())
    bucket.label = bucket.label || edge.card?.primaryName || edge.label
  })

  // ---- Pass 2: fold by **undirected** key. An undirected pair has at most two
  // directions, so there is at most one "other direction". ----
  const folded = new Map<string, DirectedPairBucket[]>()
  buckets.forEach((bucket) => {
    const key = canonicalCompoundPairKey(bucket.sourceId, bucket.targetId)
    const list = folded.get(key)
    if (list) list.push(bucket)
    else folded.set(key, [bucket])
  })

  // Dominant direction = the one with the most records; ties broken by endpoint name order, so the result is independent of payload order.
  const bucketWeight = (bucket: DirectedPairBucket) => (bucket.group ? bucket.count : Math.max(bucket.count, 1))
  const pairs: PairEntry[] = []
  folded.forEach((list, key) => {
    const ordered = [...list].sort((a, b) => bucketWeight(b) - bucketWeight(a) || a.sourceId.localeCompare(b.sourceId) || a.targetId.localeCompare(b.targetId))
    const primary = ordered[0]
    const reversed = ordered.find((bucket) => bucket.sourceId !== primary.sourceId || bucket.targetId !== primary.targetId) ?? null
    if (!reversed) {
      pairs.push({ key, sourceId: primary.sourceId, targetId: primary.targetId, label: primary.label, count: primary.count, enzymeCount: primary.enzymeCount, edgeGroupId: primary.group?.edgeGroupId ?? null, edgeGroupIds: primary.group ? [primary.group.edgeGroupId] : [], edgeIds: primary.edgeIds, edges: primary.edges, reverseSourceId: null, reverseTargetId: null })
      return
    }
    const edgeGroupIds = Array.from(new Set(ordered.map((bucket) => bucket.group?.edgeGroupId).filter((id): id is string => Boolean(id))))
    const edgeIds = Array.from(new Set(ordered.flatMap((bucket) => bucket.edgeIds)))
    const edges = Array.from(new Map(ordered.flatMap((bucket) => bucket.edges).map((edge) => [edge.edgeId, edge])).values())
    const complete = ordered.every((bucket) => bucket.complete)
    // One record = one edge_id (both directions of `unknown` share it), so the edgeId
    // union is the true record count for this compound pair -- synonymous with the
    // backend `count`. When the payload is truncated the union is unknown, so it falls
    // back to the dominant direction's value: prefer too small, never inflate, because
    // the map line is exactly `enzyme*${count}`.
    const count = complete ? edgeIds.length : Math.max(...ordered.map((bucket) => bucket.count))
    // Only when the `items` of both sides' groups are complete may the enzyme union be
    // used to recompute the label; otherwise the dominant direction's label is kept and
    // no number the payload cannot produce is invented.
    const itemsKnown = ordered.every((bucket) => bucket.group && bucket.complete && (bucket.group.items?.length ?? 0) >= bucket.group.count)
    // The map line `enzyme*N` wants the count of **distinct enzymes**, not records (see
    // `PairEntry.enzymeCount`). When the payload proves completeness, dedupe by items --
    // the two folded directions share the same records, so the union is required here;
    // same principle as `count`: if unprovable, prefer too small, never inflate (fall
    // back to the max enzyme count across directions).
    const enzymeCount = itemsKnown
      ? new Set(ordered.flatMap((bucket) => (bucket.group?.items ?? []).map((item) => item.enzymeId))).size
      : Math.max(...ordered.map((bucket) => bucket.enzymeCount))
    const label = itemsKnown
      ? (() => {
          const items = ordered.flatMap((bucket) => bucket.group?.items ?? [])
          const enzymeIds = new Set(items.map((item) => item.enzymeId))
          if (enzymeIds.size > 1) return `${enzymeIds.size}×enzyme`
          const only = items.find((item) => item.enzymeId === [...enzymeIds][0])
          return only?.label || only?.enzymeId || primary.label
        })()
      : primary.label
    pairs.push({ key, sourceId: primary.sourceId, targetId: primary.targetId, label, count, enzymeCount, edgeGroupId: edgeGroupIds[0] ?? null, edgeGroupIds, edgeIds, edges, reverseSourceId: reversed.sourceId, reverseTargetId: reversed.targetId })
  })

  return pairs.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label) || a.key.localeCompare(b.key))
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

/** Seed the home map from the graph's own tree, instead of a golden-angle spiral.
 *
 * The default map paints ~120 compounds over ~164 edges, and it is a forest rather
 * than a deep hierarchy: the biggest component holds ~85 compounds (71%), the rest
 * are small islands — a 6, a 4, a 3, then pairs — the highest degree anywhere is 10,
 * and 54 compounds are degree-1 leaves. A golden-angle spiral throws that structure
 * away and leaves the force pass to rediscover it, which it does not really do: it
 * settles into whichever local minimum it drifts into.
 *
 * Seeding from the graph's own tree starts every leaf inside its parent's angular
 * sector, which is what collapses the crossings — a spoke and its siblings never
 * have to cross to reach their parent. Measured on the live page, same node set and
 * same collision target: the spiral seed leaves 1270 straight-line crossings over
 * 8137 units of edge, this one leaves 71 over 3841, and the minimum node gap comes
 * out at the requested 14.00 either way.
 *
 * The force pass still runs afterwards and nudges things, so this only has to get
 * the ORDER roughly right, not the spacing.
 *
 * Sibling order comes from degree and then from the caller's `nodes` order, never
 * from Set iteration order — that is engine-specific, and relying on it would make
 * the map differ between browsers. Verified: reloading the page reproduces every
 * coordinate exactly.
 */
function createRadialHomeSeed(
  nodes: HomeGraphCompound[],
  links: ForceLayoutLink[],
  degreeScore: Map<string, number>,
  center: Point,
): Record<string, Point> {
  const seed: Record<string, Point> = {}
  if (nodes.length === 0) return seed

  const neighbours = new Map<string, Set<string>>()
  links.forEach((link) => {
    if (!neighbours.has(link.sourceId)) neighbours.set(link.sourceId, new Set())
    if (!neighbours.has(link.targetId)) neighbours.set(link.targetId, new Set())
    neighbours.get(link.sourceId)?.add(link.targetId)
    neighbours.get(link.targetId)?.add(link.sourceId)
  })

  const rank = new Map(nodes.map((node, index) => [node.compoundId, index]))
  const compare = (a: string, b: string) =>
    (degreeScore.get(b) || 0) - (degreeScore.get(a) || 0) || (rank.get(a) || 0) - (rank.get(b) || 0)

  // `nodes` arrives sorted by degree desc, so index 0 is the busiest compound and
  // sits in the biggest component — the one worth giving the middle to. Rooting the
  // tree anywhere else would fan the map out around a leaf.
  const rootId = nodes[0]?.compoundId
  if (!rootId) return seed

  const depth = new Map<string, number>([[rootId, 0]])
  const children = new Map<string, string[]>()
  const queue: string[] = [rootId]
  while (queue.length > 0) {
    const current = queue.shift() as string
    const kids: string[] = []
    const adjacent = neighbours.get(current)
    if (adjacent) {
      const ordered = [...adjacent].sort(compare)
      ordered.forEach((neighbour) => {
        if (depth.has(neighbour)) return
        depth.set(neighbour, (depth.get(current) || 0) + 1)
        kids.push(neighbour)
        queue.push(neighbour)
      })
    }
    children.set(current, kids)
  }

  // Compounds the BFS never reached — the map is a forest, and the root's tree holds
  // ~85 of the ~120 compounds — still need a slot. Hanging them off the root puts them
  // on its ring in sectors of their own instead of stacking on the centre point.
  const orphans = nodes.map((node) => node.compoundId).filter((id) => !depth.has(id))
  if (orphans.length > 0) {
    orphans.forEach((id) => {
      depth.set(id, 1)
      children.set(id, [])
    })
    children.set(rootId, [...(children.get(rootId) || []), ...orphans])
  }

  // Weight each node by the size of its subtree, so a hub's sector is as wide as
  // the fan it has to hold and thin branches are not squeezed into slivers.
  const weight = new Map<string, number>()
  const weigh = (id: string): number => {
    const cached = weight.get(id)
    if (cached !== undefined) return cached
    const kids = children.get(id) || []
    const size = 1 + kids.reduce((sum, kid) => sum + weigh(kid), 0)
    weight.set(id, size)
    return size
  }
  weigh(rootId)

  const angle = new Map<string, number>()
  const place = (id: string, from: number, to: number) => {
    const kids = children.get(id) || []
    if (kids.length === 0) return
    const inner = Math.max(weigh(id) - 1, 1)
    let cursor = from
    kids.forEach((kid) => {
      const share = ((to - from) * weigh(kid)) / inner
      angle.set(kid, cursor + share / 2)
      place(kid, cursor, cursor + share)
      cursor += share
    })
  }
  place(rootId, 0, Math.PI * 2)

  // Push each layer far enough out to hold its own population, and keep the radii
  // strictly increasing so a child never lands inside its parent.
  const layers = new Map<number, string[]>()
  depth.forEach((level, id) => {
    if (!layers.has(level)) layers.set(level, [])
    layers.get(level)?.push(id)
  })
  const radius = new Map<string, number>([[rootId, 0]])
  let previousRadius = 0
  const levels = [...layers.keys()].sort((a, b) => a - b)
  levels.forEach((level) => {
    if (level === 0) return
    const ids = layers.get(level) || []
    previousRadius = Math.max(previousRadius + 14, (ids.length * 9) / (2 * Math.PI))
    ids.forEach((id) => radius.set(id, previousRadius))
  })

  // Rescale to the same extent the spiral seed covered, so the force pass sees the
  // inter-node distances its constants were tuned for.
  const raw = nodes.map((node) => {
    const r = radius.get(node.compoundId) || 0
    const a = angle.get(node.compoundId) || 0
    return { id: node.compoundId, x: Math.cos(a) * r, y: Math.sin(a) * r }
  })
  const xs = raw.map((point) => point.x)
  const ys = raw.map((point) => point.y)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const scale = 100 / Math.max(maxX - minX, maxY - minY, 1)
  const midX = (minX + maxX) / 2
  const midY = (minY + maxY) / 2
  raw.forEach((point) => {
    seed[point.id] = { x: center.x + (point.x - midX) * scale, y: center.y + (point.y - midY) * scale }
  })
  return seed
}

function createInitialHomePositions(nodes: HomeGraphCompound[], graph: HomeGraphData) {
  const positions: Record<string, Point> = {}
  const velocities: Record<string, Point> = {}
  const center = { x: 50, y: 58 }
  const visibleIds = new Set(nodes.map((node) => node.compoundId))
  const links = buildForceLayoutLinks(graph, visibleIds)
  const degreeScore = buildHomeDegreeScore(graph)
  const seed = createRadialHomeSeed(nodes, links, degreeScore, center)

  nodes.forEach((node) => {
    positions[node.compoundId] = seed[node.compoundId] || { x: center.x, y: center.y }
    velocities[node.compoundId] = { x: 0, y: 0 }
  })

  /* The repulsion term dominates this pass: it is evaluated for every pair, which at
     730 compounds is 266k pairs x 520 iterations — 15.3s measured, three quarters of
     the whole layout.

     The force law is deliberately left alone here. What made it slow was reaching
     every field through the string-keyed maps on every pair: four lookups for the two
     positions and two velocities, plus two more for the degrees. Flattening the points
     into typed arrays once, and indexing those in the inner loop, drops that per-pair
     overhead without changing a single force, so the layout stays bit-identical. */
  const activeIds: string[] = []
  nodes.forEach((node) => {
    if (!node) return
    if (!positions[node.compoundId] || !velocities[node.compoundId]) return
    activeIds.push(node.compoundId)
  })
  const activeCount = activeIds.length
  const pointX = new Float64Array(activeCount)
  const pointY = new Float64Array(activeCount)
  const velocityX = new Float64Array(activeCount)
  const velocityY = new Float64Array(activeCount)
  const activeDegree = new Float64Array(activeCount)
  activeIds.forEach((id, slot) => { activeDegree[slot] = degreeScore.get(id) || 1 })

  /* How far apart each pair is in the graph, so the collision term can grade the
     berth instead of testing one bit. The shipped berths are flat, so what the loop
     below actually consumes is the island-vs-not bit; the hop distance rides along
     as the knob for it (see HOME_HOP_BERTH). The inner loop needs this per pair, so
     it cannot be a string-keyed lookup — that would be 23M string builds at 300
     compounds. One byte per ordered pair is 90KB here and 0.5MB on the whole
     730-compound graph, and reduces the test to an integer index. Both directions
     are filled so the lookup never has to order its two slots. */
  const hopFields = buildHomeHopFields(activeIds, links)

  for (let iteration = 0; iteration < HOME_FORCE_ITERATIONS; iteration += 1) {
    const heat = 1 - iteration / HOME_FORCE_ITERATIONS
    for (let slot = 0; slot < activeCount; slot += 1) {
      const id = activeIds[slot] as string
      const point = positions[id] as Point
      const velocity = velocities[id] as Point
      pointX[slot] = point.x
      pointY[slot] = point.y
      velocityX[slot] = velocity.x
      velocityY[slot] = velocity.y
    }
    for (let first = 0; first < activeCount; first += 1) {
      const firstX = pointX[first] as number
      const firstY = pointY[first] as number
      for (let second = first + 1; second < activeCount; second += 1) {
        let dx = firstX - (pointX[second] as number)
        let dy = firstY - (pointY[second] as number)
        if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) {
          const jitter = stableJitter(`${activeIds[first]}:${activeIds[second]}`)
          dx = jitter.x || 0.1
          dy = jitter.y || 0.1
        }
        const distanceSq = clamp(dx * dx + dy * dy, 12, 2200)
        const distance = Math.sqrt(distanceSq)
        const force = (HOME_FORCE_REPULSION * heat) / distanceSq
        const forceX = (dx / distance) * force
        const forceY = (dy / distance) * force
        velocityX[first] += forceX
        velocityY[first] += forceY
        velocityX[second] -= forceX
        velocityY[second] -= forceY

        /* The collision term only ever fires below collisionDistance, and the degree
           bonus adds at most 2.2 on top of the base distance, so every pair further
           out than that can skip the log2 entirely — it is the same test, just
           evaluated in the cheap order. The base is floored at the old flat 21.6 so
           no pair is pushed LESS than it used to be; only the pairs the graded berth
           rates wider than that — the cross-component ones — actually change here.
           This pass only has to pre-arrange the map for the grid pass below. */
        const baseCollisionDistance = Math.max(
          HOME_FORCE_COLLISION_DISTANCE,
          homeBerthForHop(hopFields[first * activeCount + second] as number),
        )
        if (distance < baseCollisionDistance + 2.2) {
          const collisionDistance = baseCollisionDistance + Math.min(2.2, Math.log2((activeDegree[first] as number) + (activeDegree[second] as number) + 1) * 0.28)
          if (distance < collisionDistance) {
            const collisionForce = (collisionDistance - distance) * HOME_FORCE_COLLISION_STRENGTH * (0.45 + heat)
            const collisionX = (dx / distance) * collisionForce
            const collisionY = (dy / distance) * collisionForce
            velocityX[first] += collisionX
            velocityY[first] += collisionY
            velocityX[second] -= collisionX
            velocityY[second] -= collisionY
          }
        }
      }
    }
    activeIds.forEach((id, slot) => {
      const velocity = velocities[id]
      if (!velocity) return
      velocity.x = velocityX[slot] as number
      velocity.y = velocityY[slot] as number
    })

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

  return relaxHomePositionCollisions(normalizeHomePositions(positions), links)
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

/** Shortest-path hop count between every pair of the given compounds, capped.
 *
 * Returns a flat `n * n` byte per ordered pair: 1..3 for compounds that are that
 * many hops apart, and `HOME_HOP_SENTINEL` for compounds in different connected
 * components (and for a compound against itself). This is what lets the berth know
 * the difference between "inside the big component" and "on another island
 * entirely" — a distinction a plain has-an-edge test cannot make, and the one the
 * shipped berths are set on. The exact hop count is not used by the shipped
 * settings; it is kept because it is what those settings were tuned against.
 *
 * One BFS per compound is O(n * (n + m)) — about 265k steps at 300 compounds and
 * 1.6M at 730, against a layout that costs seconds — and it is fully deterministic,
 * unlike anything derived from Map iteration order. */
function buildHomeHopFields(ids: string[], links: ForceLayoutLink[]): Uint8Array {
  const count = ids.length
  const slotOfId = new Map<string, number>()
  ids.forEach((id, slot) => slotOfId.set(id, slot))
  const adjacency: number[][] = ids.map(() => [])
  links.forEach((link) => {
    const sourceSlot = slotOfId.get(link.sourceId)
    const targetSlot = slotOfId.get(link.targetId)
    if (sourceSlot === undefined || targetSlot === undefined || sourceSlot === targetSlot) return
    adjacency[sourceSlot]?.push(targetSlot)
    adjacency[targetSlot]?.push(sourceSlot)
  })

  const cappedHop = HOME_HOP_BERTH.length - 1
  const fields = new Uint8Array(count * count).fill(HOME_HOP_SENTINEL)
  const hop = new Int16Array(count)
  const queue = new Int32Array(count)
  for (let source = 0; source < count; source += 1) {
    hop.fill(-1)
    hop[source] = 0
    queue[0] = source
    let head = 0
    let tail = 1
    while (head < tail) {
      const current = queue[head] as number
      head += 1
      const nextHop = (hop[current] as number) + 1
      // Past the cap nothing further out can change a berth, so stop expanding.
      if (nextHop > cappedHop) continue
      const neighbours = adjacency[current] as number[]
      for (let index = 0; index < neighbours.length; index += 1) {
        const neighbour = neighbours[index] as number
        if (hop[neighbour] !== -1) continue
        hop[neighbour] = nextHop
        fields[source * count + neighbour] = nextHop
        fields[neighbour * count + source] = nextHop
        queue[tail] = neighbour
        tail += 1
      }
    }
  }
  return fields
}

/** The minimum berth for a pair, given their hop field entry. */
function homeBerthForHop(hop: number) {
  if (hop === HOME_HOP_SENTINEL) return HOME_HOP_CROSS_COMPONENT
  return HOME_HOP_BERTH[Math.min(hop, HOME_HOP_BERTH.length - 1)] as number
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
  const box = homeLayoutBoxFor(points.length)
  const scaleX = ((box.width - HOME_LAYOUT_MARGIN * 2) / width) * 0.96
  const scaleY = ((box.height - HOME_LAYOUT_MARGIN * 2) / height) * 0.96
  const sourceCenter = { x: minX + width / 2, y: minY + height / 2 }
  const targetCenter = { x: HOME_VIEWBOX_WIDTH / 2, y: HOME_VIEWBOX_HEIGHT / 2 }
  const normalized: Record<string, Point> = {}
  Object.entries(positions).forEach(([compoundId, point]) => {
    normalized[compoundId] = {
      x: clamp(targetCenter.x + (point.x - sourceCenter.x) * scaleX, box.minX + HOME_LAYOUT_MARGIN, box.maxX - HOME_LAYOUT_MARGIN),
      y: clamp(targetCenter.y + (point.y - sourceCenter.y) * scaleY, box.minY + HOME_LAYOUT_MARGIN, box.maxY - HOME_LAYOUT_MARGIN),
    }
  })
  return normalized
}

function relaxHomePositionCollisions(positions: Record<string, Point>, links: ForceLayoutLink[]) {
  const next = Object.fromEntries(Object.entries(positions).map(([compoundId, point]) => [compoundId, { ...point }])) as Record<string, Point>
  const entries = Object.entries(next)
  const count = entries.length
  const gap = HOME_FINAL_COLLISION_DISTANCE
  /* The grid has one cell size but several thresholds, so the cell has to be sized
     by the widest of them: the 3x3 sweep only guarantees finding every pair closer
     than a cell. Sizing by the widest berth costs a few more candidates per cell
     and keeps the "no pair closer than its own berth is ever skipped" guarantee. */
  const cellSize = Math.max(gap, HOME_HOP_CROSS_COMPONENT)
  const entryIds = entries.map(([compoundId]) => compoundId)
  const hopFields = buildHomeHopFields(entryIds, links)
  const box = homeLayoutBoxFor(count)
  const minX = box.minX + HOME_LAYOUT_MARGIN
  const maxX = box.maxX - HOME_LAYOUT_MARGIN
  const minY = box.minY + HOME_LAYOUT_MARGIN
  const maxY = box.maxY - HOME_LAYOUT_MARGIN

  /* A pair only pushes when it is closer than `gap`, so the pass never has to look
     at all n(n-1)/2 pairs — bucketing the points into a grid of `gap`-sized cells
     and visiting just the 3x3 block around each point finds every such pair
     (anything within `gap` shares a cell or touches a neighbouring one). That turns
     each iteration from O(n^2) into about O(n), and no pair closer than `gap` is
     ever skipped. At 730 compounds this was 5.5s of a 20.8s layout.

     Pairs are still visited in ascending order, so the pass settles on the same
     arrangement — at 120 compounds the crossings, minimum gap and total edge length
     all come out identical. The coordinates are not bit-identical (max ~0.2 of a
     256-unit box): the grid is a snapshot of the iteration's start, so a pair that
     only closes mid-iteration has its correction deferred by one round. Both reach
     a 14.00 gap, which is the fixed point that matters. */
  const buckets = new Map<number, number[]>()
  // Cell coordinates stay inside the clamped box, so a fixed stride key is enough
  // and avoids building a string per lookup (2.7M of them at 730 compounds).
  const STRIDE = 4096
  const OFFSET = 1024
  const cellKey = (cx: number, cy: number) => (cx + OFFSET) * STRIDE + (cy + OFFSET)
  const cellOf = (value: number) => Math.floor(value / cellSize)

  for (let iteration = 0; iteration < HOME_FINAL_COLLISION_ITERATIONS; iteration += 1) {
    let moved = false
    const heat = 1 - iteration / HOME_FINAL_COLLISION_ITERATIONS
    buckets.clear()
    for (let index = 0; index < count; index += 1) {
      const point = entries[index]?.[1]
      if (!point) continue
      const key = cellKey(cellOf(point.x), cellOf(point.y))
      const bucket = buckets.get(key)
      if (bucket) bucket.push(index)
      else buckets.set(key, [index])
    }
    const candidates: number[] = []
    for (let first = 0; first < count; first += 1) {
      const [sourceId, source] = entries[first] || []
      if (!sourceId || !source) continue
      const cx = cellOf(source.x)
      const cy = cellOf(source.y)
      candidates.length = 0
      for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
        for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
          const bucket = buckets.get(cellKey(cx + offsetX, cy + offsetY))
          if (!bucket) continue
          for (let index = 0; index < bucket.length; index += 1) {
            const candidate = bucket[index]
            if (candidate !== undefined && candidate > first) candidates.push(candidate)
          }
        }
      }
      // Buckets are each ascending but the 3x3 sweep is not, and the original pass
      // worked strictly in ascending `second` order. Restoring that order is what
      // keeps this a refactor rather than a behaviour change.
      candidates.sort((left, right) => left - right)
      for (let index = 0; index < candidates.length; index += 1) {
        const second = candidates[index]
        if (second === undefined) continue
        const [targetId, target] = entries[second] || []
        if (!targetId || !target) continue
        let dx = source.x - target.x
        let dy = source.y - target.y
        let distance = Math.hypot(dx, dy)
        if (distance < 0.001) {
          const jitter = stableJitter(`${sourceId}:${targetId}:final`)
          dx = jitter.x || 0.1
          dy = jitter.y || 0.1
          distance = Math.hypot(dx, dy)
        }
        // The berth is graded by graph distance, not by has-a-line: two compounds on
        // different islands get the widest one. That separation stretches no edge,
        // but it is not free — the box is a fixed size, so the room comes out of
        // the big component. See HOME_HOP_BERTH.
        const pairGap = Math.max(gap, homeBerthForHop(hopFields[first * count + second] as number))
        if (distance >= pairGap) continue
        const shift = ((pairGap - distance) / 2) * (0.35 + heat * 0.65)
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
      point.x = clamp(point.x, minX, maxX)
      point.y = clamp(point.y, minY, maxY)
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

/**
 * Fold the per-enzyme groups of **auto-annotated sources** (non Swiss-Prot) in each
 * edge group into a single summary unit.
 *
 * Why folding is required: the largest group measured, `RHEA:32299`, carries **3,595
 * enzymes** (3,5xx of them TrEMBL), and each enzyme renders 1 card + 2 `<path>` + 1
 * `<text>` -- one expansion is 3,595 cards + 7,190 paths + 3,595 labels. Worse, the
 * fan offset `((i - (n-1)/2) * 5.2, see the SVG-side offsets)`: at n=3,595 the outermost
 * line is pushed **±9,342 units** out, far past the viewbox -- so an expansion of this
 * scale is not merely slow, it is unreadable by itself.
 *
 * Swiss-Prot stays per-record: it is the manually reviewed batch, small by nature
 * (measured, 60 records in the largest group), readable as-is. Every other source folds
 * to **one unit per source** (not just trembl) -- so when `ai_literature` really has
 * bulk data one day it folds the same way automatically, with no change to this rule.
 *
 * Merged unit count = 1 + the number of distinct non Swiss-Prot sources in the group +
 * the number of Swiss-Prot enzymes. In the example above, 7,093 drops to 61.
 *
 * ⚠️ This layer is **render-only**: the data layer `expandedEdgeGroups` is unchanged,
 * still the per-enzyme truth, and the detail panel / filters / download all still read
 * it. So the filter semantics are entirely unchanged -- filtering acts on `pairEdges`
 * and happens before merging, and scoping to swiss_prot leaves no TrEMBL edge at all, so
 * no merged card is produced.
 */
function mergeAutoAnnotatedUnits(groups: ExpandedEdgeGroup[]): ExpandedEdgeGroup[] {
  if (groups.length === 0) return groups

  const singles: ExpandedEdgeGroup[] = []
  const buckets = new Map<string, ExpandedEdgeGroup[]>()
  groups.forEach((group) => {
    const sourceType = group.representative.sourceType
    // Unknown source is treated as Swiss-Prot (shown per-record) -- better to render
    // more than to fold enzymes of different sources into one card over a null.
    if (!sourceType || sourceType === 'swiss_prot') {
      singles.push(group)
      return
    }
    const bucket = buckets.get(sourceType)
    if (bucket) bucket.push(group)
    else buckets.set(sourceType, [group])
  })

  if (buckets.size === 0) return singles

  const merged: ExpandedEdgeGroup[] = [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([sourceType, members]) => {
      const first = members[0]
      const edges = members.flatMap((member) => member.edges)
      const edgeIds = Array.from(new Set(members.flatMap((member) => member.edgeIds)))
      const reactionIds = Array.from(new Set(members.flatMap((member) => member.reactionIds)))
      return {
        key: `merged::${sourceType}::${canonicalCompoundPairKey(first.sourceId, first.targetId)}`,
        sourceId: first.sourceId,
        targetId: first.targetId,
        // Kept for structural compatibility; the merged branch does not read it.
        enzymeId: first.enzymeId,
        // With a single member, use the **member's own label**: the user chose "merge
        // whenever there is TrEMBL" (no threshold), so a group with just one TrEMBL
        // folds too -- swapping the enzyme name for "TrEMBL ×1" then throws information
        // away for nothing. The look stays a merged card (with the source badge).
        label: members.length > 1 ? `${sourceLabel(sourceType)} ×${members.length}` : first.label,
        directionMode: directionModeForEdges(edges, first.sourceId, first.targetId),
        edges,
        edgeIds,
        reactionIds,
        representative: first.representative,
        merged: true,
        sourceType,
        memberCount: members.length,
        members,
      }
    })

  // Deterministic order (no drift with scan order): Swiss-Prot per-record keeps
  // groupExpandedEdgesByEnzyme's existing label order first, merged units follow in
  // source-name lexical order.
  return [...singles, ...merged]
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
      // The group id of the other folded direction should be searchable too (otherwise
      // searching `GROUP_B_A` misses the compound pair already merged into one line).
      const pairValues = [pair.label, ...pair.edgeGroupIds, pair.sourceId, pair.targetId, sourceName, targetName]
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
  // `PairEntry.key` is already undirected, both directions land on the same one -- a
  // single lookup suffices (the old "reverse fallback" now hits the same row, redundant).
  return pairs.find((pair) => pair.key === canonicalCompoundPairKey(sourceId, targetId)) || null
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
      ...pair.edgeGroupIds,
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
function svgPointerDelta(svg: SVGSVGElement, startClientX: number, startClientY: number, clientX: number, clientY: number, scale = 1) {
  const rect = svg.getBoundingClientRect()
  const safeScale = Math.max(scale, 0.001)
  return {
    x: (((clientX - startClientX) / Math.max(rect.width, 1)) * HOME_VIEWBOX_WIDTH) / safeScale,
    y: (((clientY - startClientY) / Math.max(rect.height, 1)) * HOME_VIEWBOX_HEIGHT) / safeScale,
  }
}
function getNodeExpansionDirection(point: Point, camera: Point, scale = 1): ExpansionDirection | null {
  // Keep in step with the camera transform in the SVG: the camera group is
  // `translate(c + W/2, c + H/2) scale(z) translate(-W/2, -H/2)`, so a world
  // point p lands at `z*p + c + (1-z)*W/2`. The plain `p + c` only holds at z=1
  // and would misjudge the viewport edge as soon as the map is zoomed.
  const safeScale = Math.max(scale, 0.001)
  const viewportPoint = {
    x: point.x * safeScale + camera.x + (1 - safeScale) * (HOME_VIEWBOX_WIDTH / 2),
    y: point.y * safeScale + camera.y + (1 - safeScale) * (HOME_VIEWBOX_HEIGHT / 2),
  }
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
      // Copied for the drawer and download page. Without it the score is lost once the enzyme is picked.
      deepSolnetScore: card?.deepSolnetScore ?? null,
      membrane: card?.membrane ?? null,
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
 * Download button. It is NOT a modal: no backdrop, no auto-close from map operation —
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
    <aside className="pw-enzyme-drawer" role="dialog" aria-label="Pick an enzyme for each pathway step">
      <header className="pw-drawer-header">
        <div>
          <strong>Select enzymes per step</strong>
          <span>Click a step below, or its edge on the map, to switch · you can change your picks at any time</span>
        </div>
        <button type="button" className="pw-drawer-close" onClick={onClose} title="Close" aria-label="Close the enzyme picker">
          <X size={18} />
        </button>
      </header>

      <div className="pw-drawer-stepper" role="tablist" aria-label={`Pathway steps, ${steps.length} total`}>
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
              title={`Step ${step.step}: ${step.sourceName} → ${step.targetName}`}
              aria-label={`Step ${step.step}, ${done ? 'picked' : 'not picked'}`}
            >
              <span className="pw-drawer-chip-tick">{done ? <Check size={12} /> : step.step}</span>
              <span className="pw-drawer-chip-name">Step {step.step}</span>
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
                <span className="pw-drawer-step-count">{selection[activeStep.step]?.length} picked</span>
              )}
            </h4>
            {activeLoading ? (
              <p className="pw-drawer-step-loading">
                <Loader2 size={14} className="pw-drawer-spin" /> Loading enzymes for this step…
              </p>
            ) : activeCandidates.length === 0 ? (
              <p className="pw-drawer-step-empty">No enzyme data for this step</p>
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
                          {(enzyme.organismName || enzyme.sourceType || enzyme.deepSolnetScore != null) && (
                            <span className="pw-drawer-candidate-sub">
                              {enzyme.organismName ? <span className="pw-drawer-candidate-organism">{enzyme.organismName}</span> : null}
                              {enzyme.sourceType ? <span className="pw-drawer-candidate-src">{enzyme.sourceType}</span> : null}
                              {enzyme.deepSolnetScore != null ? (
                                <span className="pw-drawer-candidate-src" title="DeepSolNet model reference score — not a solubility label">
                                  DeepSolNet Score {enzyme.deepSolnetScore.toFixed(3)}{enzyme.membrane === MEMBRANE_VALUE ? ' · membrane' : ''}
                                </span>
                              ) : null}
                            </span>
                          )}
                        </span>
                      </label>
                      <button
                        type="button"
                        className="pw-drawer-candidate-open"
                        onClick={() => onOpenEnzyme(enzyme.enzymeId)}
                        title={`View detail for ${entryLabel}`}
                        aria-label={`View detail for ${entryLabel}`}
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
          {chosenStepCount}/{steps.length} steps picked
        </span>
        <button type="button" className="pw-drawer-add" disabled={!canAdd} onClick={handleAdd}>
          <Check size={15} /> Add to downloading table
        </button>
      </footer>
    </aside>
  )
}

/** Page size for the merged-unit drawer. 3,595 rows of `.pw-drawer-candidate`
 *  is a rerun of the very stutter this feature removes, so the list renders in
 *  batches and offers a text filter to converge on one entry. */
const MERGED_DRAWER_PAGE = 100

/**
 * After "a whole batch of enzymes from one source" folds into a card, this is the
 * drawer that card opens -- the merged enzymes must stay reachable: viewable,
 * selectable, and able to enter the download table. It does **not** reuse
 * `PathwayEnzymePickerDrawer`: that one is tied to multi-step pathway selection
 * (`steps` / per-step `selection` / `handleAdd` assembling a Pathway Entity), so only
 * its CSS classes and row structure are borrowed.
 *
 * One row = one enzyme. `unit.members` is already deduped by enzymeId via
 * `groupExpandedEdgesByEnzyme`, so it is one enzyme per row by construction and needs
 * no further dedupe; and each row keeps its backing edge (which `homeEnzymeToEntity`
 * needs to enter the download queue).
 */
function MergedEnzymeDrawer({
  unit,
  compoundLabel,
  onClose,
  onOpenEnzyme,
  onToggleQueue,
  isQueued,
  onQueueMany,
}: {
  unit: ExpandedEdgeGroup
  compoundLabel: (compoundId: string) => string
  onClose: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onToggleQueue: (entry: Entity) => void
  isQueued: (enzymeId: string) => boolean
  /** Bulk queue. Calling `onToggleQueue` per entry is O(n²) (see App's `queueEntities`). */
  onQueueMany: (entries: Entity[]) => void
}) {
  const members = unit.members ?? []
  const [filter, setFilter] = useState('')
  const [visibleCount, setVisibleCount] = useState(MERGED_DRAWER_PAGE)

  // A different merged unit is a different list: the filter box and batch cursor both reset with it.
  useEffect(() => {
    setFilter('')
    setVisibleCount(MERGED_DRAWER_PAGE)
  }, [unit.key])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return members
    return members.filter((member) => {
      const card = member.representative.card
      return [member.label, card?.primaryName, card?.uniprotId, card?.databaseCode, card?.organismName, card?.enzymeId, member.enzymeId]
        .some((value) => (value || '').toLowerCase().includes(needle))
    })
  }, [members, filter])

  const visible = rows.slice(0, visibleCount)
  const queuedCount = rows.reduce((count, member) => count + (isQueued(member.representative.card?.enzymeId || member.enzymeId) ? 1 : 0), 0)

  const entityOf = (member: ExpandedEdgeGroup) => {
    const edge = member.representative
    const enzymeId = edge.card?.enzymeId || edge.enzymeId
    return homeEnzymeToEntity(edge, enzymeId, compoundLabel(edge.sourceCompoundId), compoundLabel(edge.targetCompoundId))
  }

  const queueAll = () => {
    // The label spells out the number; no silent bulk action.
    onQueueMany(rows.map(entityOf))
    onClose()
  }

  return (
    <aside className="pw-enzyme-drawer merged-enzyme-drawer" role="dialog" aria-label={`All ${members.length} entries in this group`}>
      <header className="pw-drawer-header">
        <div>
          <strong>{sourceLabel(unit.sourceType || '')} group</strong>
          <span>{compoundLabel(unit.sourceId)} → {compoundLabel(unit.targetId)}</span>
        </div>
        <button type="button" className="pw-drawer-close" onClick={onClose} title="Close" aria-label="Close the entry list">
          <X size={17} />
        </button>
      </header>

      <div className="merged-drawer-bar">
        <div className="pw-drawer-candidates-search">
          <Search size={13} />
          <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by accession, name or organism…" />
          {filter && (
            <button type="button" onClick={() => setFilter('')} title="Clear filter">
              <X size={12} />
            </button>
          )}
        </div>
        <p className="merged-drawer-count">
          {rows.length === members.length
            ? `${members.length.toLocaleString()} entries`
            : `${rows.length.toLocaleString()} of ${members.length.toLocaleString()} entries`}
          {queuedCount > 0 ? ` · ${queuedCount.toLocaleString()} in the download list` : ''}
        </p>
      </div>

      <div className="pw-drawer-body">
        {visible.length === 0 ? (
          <p className="pw-drawer-step-empty">Nothing matches “{filter}”.</p>
        ) : (
          <div className="pw-drawer-candidates">
            {visible.map((member) => {
              const edge = member.representative
              const enzymeId = edge.card?.enzymeId || edge.enzymeId
              const entryLabel = edge.card?.uniprotId || edge.card?.databaseCode || member.label || enzymeId
              const organism = edge.card?.organismName
              const checked = isQueued(enzymeId)
              return (
                <div key={member.key} className={`pw-drawer-candidate ${checked ? 'is-checked' : ''}`}>
                  <label className="pw-drawer-candidate-main">
                    <input type="checkbox" checked={checked} onChange={() => onToggleQueue(entityOf(member))} />
                    <span className="pw-drawer-candidate-copy">
                      <strong className="pw-drawer-candidate-entry">{entryLabel}</strong>
                      {edge.card?.primaryName && edge.card.primaryName !== entryLabel ? (
                        <em className="pw-drawer-candidate-name">{edge.card.primaryName}</em>
                      ) : null}
                      <span className="pw-drawer-candidate-sub">
                        {organism ? <span className="pw-drawer-candidate-organism">{organism}</span> : null}
                        <span className="pw-drawer-candidate-src">{sourceLabel(edge.sourceType)}</span>
                        {edge.card?.deepSolnetScore != null ? (
                          <span className="pw-drawer-candidate-src" title="DeepSolNet model reference score — not a solubility label">
                            DeepSolNet Score {edge.card.deepSolnetScore.toFixed(3)}{edge.card.membrane === MEMBRANE_VALUE ? ' · membrane' : ''}
                          </span>
                        ) : null}
                      </span>
                    </span>
                  </label>
                  <button
                    type="button"
                    className="pw-drawer-candidate-open"
                    onClick={() => onOpenEnzyme(enzymeId)}
                    title={`Open ${entryLabel}`}
                    aria-label={`Open ${entryLabel}`}
                  >
                    <ArrowUpRight size={13} />
                  </button>
                </div>
              )
            })}
          </div>
        )}
        {visibleCount < rows.length && (
          <button type="button" className="merged-drawer-more" onClick={() => setVisibleCount((count) => count + MERGED_DRAWER_PAGE)}>
            Show {Math.min(MERGED_DRAWER_PAGE, rows.length - visibleCount)} more
            <small>{(rows.length - visibleCount).toLocaleString()} left</small>
          </button>
        )}
      </div>

      <footer className="pw-drawer-footer">
        <span className="pw-drawer-count">
          {queuedCount.toLocaleString()} / {rows.length.toLocaleString()} in the download list
        </span>
        <button type="button" className="pw-drawer-add" onClick={queueAll} disabled={rows.length === 0}>
          <Download size={15} /> Queue all {rows.length.toLocaleString()} entries
        </button>
      </footer>
    </aside>
  )
}



















