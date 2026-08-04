import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  Dna,
  Download,
  ExternalLink,
  Link2,
  Loader2,
  Network,
  Search,
  X,
} from 'lucide-react'
import {
  createEnzymeDownload,
  loadExpandedEdgeGroup,
  loadEnzymeDetail,
  loadHomeGraph,
  searchApiEntries,
  searchHomePathways,
  type EnzymeDetailData,
  type EnzymeSequenceLink,
  type HomeGraphCompound,
  type HomeGraphData,
  type HomeGraphEdge,
  type HomePathwayCard,
} from './api'
import type { Entity, EntityKind } from './types'

const HOME_EXPANSION_LIMIT = 36
const HOME_VIEWBOX_WIDTH = 100
const HOME_VIEWBOX_HEIGHT = 118
const HOME_IMPORTANT_LABEL_COUNT = 10
const HOME_FORCE_ITERATIONS = 340
const HOME_FORCE_REPULSION = 82
const HOME_FORCE_LINK_DISTANCE = 32
const HOME_FORCE_LINK_STRENGTH = 0.0048
const HOME_FORCE_CENTERING = 0.00055
const HOME_FORCE_DAMPING = 0.66
const HOME_FORCE_COLLISION_DISTANCE = 7.2
const HOME_FORCE_COLLISION_STRENGTH = 0.34
const HOME_FINAL_COLLISION_DISTANCE = 7.2
const HOME_FINAL_COLLISION_ITERATIONS = 260
const HOME_GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5))
const homeSearchModes = [
  { id: 'enzymeItems', label: 'Enzyme items' },
  { id: 'pathways', label: 'Pathways' },
  { id: 'blast', label: 'Blast / homology' },
  { id: 'mapsearch', label: 'Map search' },
] as const
const homeDatasetOptions = [
  { id: 'terpene_synthase', label: 'Terpene synthase', detail: 'Live backend', disabled: false },
  { id: 'comparative_sets', label: 'Comparative sets', detail: 'Coming soon', disabled: true },
  { id: 'literature_merge', label: 'Literature merge', detail: 'Coming soon', disabled: true },
] as const
const homeSearchFilters = [
  { id: 'all', label: 'All' },
  { id: 'compound', label: 'Compounds' },
  { id: 'enzyme', label: 'Enzymes' },
  { id: 'reaction', label: 'Reactions' },
] as const
type HomeSearchMode = (typeof homeSearchModes)[number]['id']
type HomeSearchFilter = (typeof homeSearchFilters)[number]['id']

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

export function CompoundGraphHome({
  onOpenSearch,
  onOpenNetwork,
  onOpenDownloads,
  onOpenEnzyme,
  onToggleQueue,
  isQueued,
  queueCount,
}: {
  onOpenSearch: (query?: string) => void
  onOpenNetwork: () => void
  onOpenDownloads: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onToggleQueue: (id: string) => void
  isQueued: (id: string) => boolean
  queueCount: number
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
  const [searchFeedback, setSearchFeedback] = useState<string | null>(null)
  const [mode, setMode] = useState<HomeSearchMode>('enzymeItems')
  const [modeOpen, setModeOpen] = useState(false)
  const [datasetOpen, setDatasetOpen] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [searchFocused, setSearchFocused] = useState(false)
  const [searchFilter, setSearchFilter] = useState<HomeSearchFilter>('all')
  const [librarySuggestions, setLibrarySuggestions] = useState<HomeSearchSuggestion[]>([])
  const [librarySearchLoading, setLibrarySearchLoading] = useState(false)
  const [selectedLibraryItem, setSelectedLibraryItem] = useState<Entity | null>(null)
  const [selectedDatasetId, setSelectedDatasetId] = useState<(typeof homeDatasetOptions)[number]['id']>(homeDatasetOptions[0].id)
  const [nodeSize, setNodeSize] = useState(1.05)
  const [labelScale, setLabelScale] = useState(1.22)
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
    graphRef.current = graph
  }, [graph])

  useEffect(() => {
    positionsRef.current = positions
  }, [positions])

  useEffect(() => {
    cameraRef.current = camera
  }, [camera])

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
  const pairEdges = selectedPairKey ? (expandedEdges.length > 0 ? expandedEdges : selectedPair?.edges ?? []) : []
  const expandedEdgeGroups = useMemo(
    () => groupExpandedEdgesByEnzyme(pairEdges, selectedPair?.sourceId, selectedPair?.targetId),
    [pairEdges, selectedPair?.sourceId, selectedPair?.targetId],
  )
  const selectedExpandedGroup = expandedEdgeGroups.find((group) => group.edgeIds.includes(selectedEdgeId || '')) || expandedEdgeGroups[0] || null
  const selectedPairTotal = selectedPair ? Math.max(selectedPair.count, selectedPair.edges.length) : 0
  const visibleEdgeCount = viewModel.pairs.reduce((sum, pair) => sum + Math.max(pair.count, pair.edges.length || 0), 0)
  const compoundName = (compoundId: string) => viewModel.nodes.find((node) => node.compoundId === compoundId)?.name || compoundId
  const selectedDataset = homeDatasetOptions.find((item) => item.id === selectedDatasetId) ?? homeDatasetOptions[0]
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
  const showSearchSuggestions = mode !== 'blast' && searchFocused && trimmedSearchValue.length > 0
  const panelStyle: CSSProperties | undefined = panelPosition
    ? { left: panelPosition.x, top: panelPosition.y, right: 'auto', bottom: 'auto' }
    : undefined

  useEffect(() => {
    if (mode === 'blast' || (searchFilter !== 'all' && searchFilter !== 'enzyme') || trimmedSearchValue.length < 2) {
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
  }, [mode, searchFilter, trimmedSearchValue])

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
    setSelectedNodeId(null)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setActivePathway(null)
    setSelectedLibraryItem(null)
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
    if (pair.edges.length > 0 && pair.edges.length === pair.count) {
      const nextEdges = pair.edges
      const selected = pickTargetEdge(nextEdges, targetEdge) || nextEdges[0] || null
      setExpandedEdges(nextEdges)
      setSelectedEdgeId(selected?.edgeId ?? null)
      setHighlightedEdgeIds(new Set(selected ? [selected.edgeId] : nextEdges.map((edge) => edge.edgeId)))
      return
    }
    if (pair.edgeGroupId) {
      setExpandedLoading(true)
      try {
        const edges = await loadExpandedEdgeGroup(pair.edgeGroupId)
        const nextEdges = edges.length > 0 ? edges : pair.edges
        const selected = pickTargetEdge(nextEdges, targetEdge) || nextEdges[0] || null
        setExpandedEdges(nextEdges)
        setSelectedEdgeId(selected?.edgeId ?? null)
        setHighlightedEdgeIds(new Set(selected ? [selected.edgeId] : nextEdges.map((edge) => edge.edgeId)))
      } finally {
        setExpandedLoading(false)
      }
      return
    }
    const nextEdges = pair.edges
    const selected = pickTargetEdge(nextEdges, targetEdge) || nextEdges[0] || null
    setExpandedEdges(nextEdges)
    setSelectedEdgeId(selected?.edgeId ?? null)
    setHighlightedEdgeIds(new Set(selected ? [selected.edgeId] : nextEdges.map((edge) => edge.edgeId)))
  }

  const handlePairClick = async (pair: PairEntry) => {
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

  const handleNodeSelect = (compoundId: string) => {
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

  const handleSearchSubmit = async () => {
    const trimmed = searchValue.trim()
    setModeOpen(false)
    if (!trimmed) {
      clearPairSelection()
      return
    }
    if (mode === 'blast') {
      onOpenSearch(trimmed)
      return
    }
    if (mode === 'pathways') {
      await handlePathwaySearch(trimmed)
      return
    }
    if (visibleSearchSuggestions.length > 0) {
      await handleSearchSuggestionSelect(visibleSearchSuggestions[0])
      return
    }
    setSearchFeedback('No matching result in the loaded map yet.')
  }

  const handleGraphSearch = (query: string) => {
    if (!graph) return
    const match = findGraphSearchMatch(query, graph, viewModel.pairs)
    if (match.kind === 'node') {
      setSelectedNodeId(match.nodeId)
      setSelectedPairKey(null)
      setExpandedEdges([])
      setSelectedEdgeId(null)
      setActivePathway(null)
      setHighlightedNodeIds(new Set([match.nodeId]))
      setHighlightedEdgeIds(new Set())
      setHighlightedEdgeGroupIds(new Set())
      setSearchFeedback(`Focused compound: ${compoundName(match.nodeId)}`)
      focusCameraOnNode(match.nodeId)
      return
    }
    if (match.kind === 'pair') {
      const pair = match.pair
      setSelectedPairKey(pair.key)
      setSelectedNodeId(null)
      setActivePathway(null)
      const nextEdges = match.edges.length > 0 ? match.edges : pair.edges
      setExpandedEdges(nextEdges)
      setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
      setHighlightedNodeIds(new Set([pair.sourceId, pair.targetId]))
      setHighlightedEdgeIds(new Set(nextEdges.map((edge) => edge.edgeId)))
      setHighlightedEdgeGroupIds(new Set([pair.edgeGroupId || pair.key]))
      setSearchFeedback(`Focused edge: ${compoundName(pair.sourceId)} -> ${compoundName(pair.targetId)}`)
      focusCameraOnPair(pair)
      return
    }
    setSearchFeedback('No match in the loaded map. Drag the map edge to expand, or open the search library.')
  }

  const handlePathwaySearch = async (query: string) => {
    if (!graph) return
    const endpoints = resolvePathwayEndpoints(query, graph.nodes)
    if (!endpoints) {
      setSearchFeedback('Pathway mode expects two compounds, for example CHEBI:15422 -> CHEBI:10280.')
      return
    }
    setLoading(true)
    try {
      const cards = await searchHomePathways(endpoints.startId, endpoints.endId)
      const pathway = cards[0]
      if (!pathway) {
        setSearchFeedback('No pathway found for those compounds.')
        return
      }
      const expansions = await Promise.all([
        loadHomeGraph({ centerCompoundId: endpoints.startId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT }),
        loadHomeGraph({ centerCompoundId: endpoints.endId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT }),
      ])
      const merged = expansions.reduce((current, payload) => mergeHomeGraph(current, payload), graphRef.current || graph)
      const withStart = addExpansionPositions(positionsRef.current, expansions[0], endpoints.startId, 'right')
      const nextPositions = addExpansionPositions(withStart, expansions[1], endpoints.endId, 'left')
      graphRef.current = merged
      positionsRef.current = nextPositions
      setGraph(merged)
      setPositions(nextPositions)
      setActivePathway(pathway)
      setSelectedNodeId(null)
      setSelectedPairKey(null)
      setExpandedEdges([])
      setSelectedEdgeId(null)
      setHighlightedNodeIds(new Set(pathway.compoundIds))
      setHighlightedEdgeIds(new Set(pathway.edgeIds))
      setHighlightedEdgeGroupIds(new Set(pathway.edgeGroupIds))
      setSearchFeedback(`Highlighted pathway: ${pathway.stepCount} steps`)
      focusCameraOnPath(pathway.compoundIds)
    } catch (err) {
      setSearchFeedback(err instanceof Error ? err.message : 'Unable to search pathway.')
    } finally {
      setLoading(false)
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

  const searchPlaceholder =
    mode === 'blast'
      ? 'Paste a protein sequence or accession'
      : mode === 'pathways'
        ? 'Search pathways, compound pairs, or reactions'
        : 'Search enzymes, substrates, or products'

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

  return (
    <div className="home-map-page">
      <section className="atlas-map-stage atlas-live-stage" aria-label="Interactive compound graph homepage">
        <div className="atlas-brand">
          <span className="atlas-logo">
            <Network size={18} />
          </span>
          <span>Starase Atlas</span>
        </div>

        <div className="atlas-year">NJU - China 2026</div>

        <div className={`floating-pill dataset-pill dataset-pill-static ${datasetOpen ? 'is-open' : ''}`}>
          <button className="dataset-pill-button" type="button" onClick={() => setDatasetOpen((open) => !open)}>
            <span>Dataset</span>
            <strong>{selectedDataset.label}</strong>
            <ChevronDown size={18} />
          </button>
          {datasetOpen && (
            <div className="floating-menu dataset-menu dataset-select-menu">
              {homeDatasetOptions.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  disabled={item.disabled}
                  className={item.id === selectedDatasetId ? 'is-active' : ''}
                  onClick={() => {
                    if (item.disabled) return
                    setSelectedDatasetId(item.id)
                    setDatasetOpen(false)
                  }}
                >
                  <span>{item.label}</span>
                  <small>{item.detail}</small>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="home-search-bar">
          <button className="home-search-mode" type="button" onClick={() => setModeOpen((open) => !open)}>
            <ChevronDown size={22} />
            <span>{homeSearchModes.find((item) => item.id === mode)?.label}</span>
          </button>
          {modeOpen && (
            <div className="floating-menu search-mode-menu">
              {homeSearchModes.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setMode(item.id)
                    setModeOpen(false)
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
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
          <button className="home-search-submit" type="button" onClick={() => void handleSearchSubmit()} title="Search">
            <Search size={34} />
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
        </div>

        <button className="floating-pill download-pill home-pill-button" type="button" onClick={onOpenDownloads}>
          Downloading table
          {queueCount > 0 && <span>{queueCount}</span>}
        </button>

        <div className={`floating-pill mapping-pill ${controlsOpen ? 'is-open' : ''}`}>
          <button type="button" onClick={() => setControlsOpen((open) => !open)}>
            <span>Graph controls</span>
            <ChevronDown size={18} />
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
                <label htmlFor="home-label-size">Label size</label>
                <div className="control-slider-row">
                  <input id="home-label-size" className="control-slider" type="range" min="0.85" max="2.1" step="0.05" value={labelScale} onChange={(event) => setLabelScale(Number(event.target.value))} />
                  <span className="control-value">{labelScale.toFixed(2)}</span>
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

        {loading && <div className="home-map-feedback"><Loader2 size={18} className="spin" /> Loading backend graph...</div>}
        {error && !loading && <div className="home-map-feedback error-state"><X size={18} /> {error}</div>}
        {mapExpanding && !loading && !error && <div className="home-map-feedback map-expanding-feedback"><Loader2 size={18} className="spin" /> Expanding map...</div>}
        {searchFeedback && !loading && !error && <div className="home-search-feedback">{searchFeedback}</div>}

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
                  const pairGroupId = pair.edgeGroupId || pair.key
                  const isExpanded = selectedPairKey === pair.key && pairEdges.length > 0
                  const expandedItems = expandedEdgeGroups
                  const offsets = expandedItems.length > 1 ? expandedItems.map((_, index) => (index - (expandedItems.length - 1) / 2) * 3.2) : [0]
                  const pairLineLabel = pair.count > 1 ? `enzyme*${pair.count}` : pair.edges[0]?.card?.primaryName || 'enzyme'
                  const highlightedPair = highlightedEdgeGroupIds.has(pairGroupId) || pair.edgeIds.some((edgeId) => highlightedEdgeIds.has(edgeId))
                  const pathwayPair = Boolean(activePathway && (activePathway.edgeGroupIds.includes(pairGroupId) || pair.edgeIds.some((edgeId) => activePathway.edgeIds.includes(edgeId))))
                  const showPairLabel = selectedPairKey === pair.key || highlightedPair || pathwayPair
                  return (
                    <g key={pair.key} className="home-map-edge-group">
                      {!isExpanded && (
                        <>
                          <path
                            d={edgePath(source, target, 0)}
                            className={`home-map-path ${pair.count > 1 ? 'multi' : ''} ${selectedPairKey === pair.key ? 'active' : ''} ${highlightedPair ? 'highlighted' : ''} ${pathwayPair ? 'pathway' : ''}`}
                            markerEnd="url(#home-map-arrow)"
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={(event) => { event.stopPropagation(); void handlePairClick(pair) }}
                          />
                          <path d={edgePath(source, target, 0)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); void handlePairClick(pair) }} />
                          <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 1.8} className={`home-edge-label ${showPairLabel ? 'is-visible' : ''}`} fontSize={1.02 * labelScale}>{pairLineLabel}</text>
                        </>
                      )}
                      {isExpanded && expandedItems.map((edge, index) => {
                        const offset = offsets[index] ?? 0
                        const highlightedEdge = highlightedPair || edge.edgeIds.some((edgeId) => highlightedEdgeIds.has(edgeId))
                        const pathwayEdge = Boolean(activePathway?.edgeIds.some((edgeId) => edge.edgeIds.includes(edgeId)))
                        const selectedEdgeGroup = edge.edgeIds.includes(selectedEdgeId || '')
                        return (
                          <g key={edge.key}>
                            <path
                              d={edgePath(source, target, offset)}
                              className={`expanded-edge live-expanded-edge ${selectedEdgeGroup ? 'selected' : ''} ${highlightedEdge ? 'highlighted' : ''} ${pathwayEdge ? 'pathway' : ''}`}
                              markerStart={edge.directionMode === 'reverse' || edge.directionMode === 'bidirectional' ? 'url(#home-map-arrow)' : undefined}
                              markerEnd={edge.directionMode === 'forward' || edge.directionMode === 'bidirectional' ? 'url(#home-map-arrow)' : undefined}
                              onPointerDown={(event) => event.stopPropagation()}
                              onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.representative.edgeId) }}
                            />
                            <path d={edgePath(source, target, offset)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.representative.edgeId) }} />
                            <text x={(source.x + target.x) / 2 + offset * 0.34} y={(source.y + target.y) / 2 + offset * 0.45 - 1.4} className="expanded-edge-label" fontSize={0.94 * labelScale}>{edge.label}</text>
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
                  const showNodeLabel = importantLabelIds.has(node.compoundId) || selected || highlighted || pathway || pairEndpoint || neighbor
                  return (
                    <g key={node.compoundId} className={`home-map-node ${selected || pairEndpoint ? 'selected' : ''} ${highlighted ? 'highlighted' : ''} ${pathway ? 'pathway' : ''} ${neighbor ? 'neighbor' : ''} ${activeNodeDragId === node.compoundId ? 'dragging' : ''}`}>
                      <circle
                        cx={pos.x}
                        cy={pos.y}
                        r={nodeSize}
                        filter={selected || highlighted || pathway || pairEndpoint ? 'url(#selected-node-glow)' : 'url(#home-node-glow)'}
                        onPointerDown={(event) => handleNodePointerDown(event, node, pos)}
                        onPointerMove={handleNodePointerMove}
                        onPointerUp={finishNodeDrag}
                        onPointerCancel={finishNodeDrag}
                      />
                      <title>{node.name}</title>
                      <text x={pos.x} y={pos.y + nodeSize + 6.3} className={`home-map-node-name ${showNodeLabel ? 'is-visible' : ''}`} fontSize={2.35 * labelScale}>
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
            <button className="popover-cart" type="button" onClick={() => onToggleQueue(selectedNode.compoundId)}>
              <span className={`check-box ${isQueued(selectedNode.compoundId) ? 'checked' : ''}`}>{isQueued(selectedNode.compoundId) && <Check size={17} />}</span>
              {isQueued(selectedNode.compoundId) ? 'In downloading table' : 'Add to downloading table'}
            </button>
          </div>
        )}

        {selectedPair && (
          <div className="enzyme-card-stack live-enzyme-stack map-draggable-panel" style={panelStyle}>
            <div className="stack-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <div>
                <strong>{compoundName(selectedPair.sourceId)} <ChevronRight size={14} /> {compoundName(selectedPair.targetId)}</strong>
                <span>{expandedLoading ? 'Loading enzyme paths...' : `${expandedEdgeGroups.length} enzymes / ${pairEdges.length || selectedPairTotal} reactions`}</span>
              </div>
              <button className="stack-close-button" type="button" onClick={clearPairSelection} title="Close enzyme list">
                <X size={18} />
              </button>
            </div>
            {expandedEdgeGroups.map((group) => {
              const edge = group.representative
              const enzymeId = edge.card?.enzymeId || edge.enzymeId
              const queued = isQueued(enzymeId)
              return (
                <article key={group.key} className={`enzyme-card ${group.edgeIds.includes(selectedEdgeId || '') ? 'selected' : ''}`}>
                  <button className="card-check" type="button" onClick={() => onToggleQueue(enzymeId)}>
                    {queued ? <Check size={18} /> : <Download size={18} />}
                  </button>
                  <button className="enzyme-card-copy" type="button" onClick={() => setSelectedEdgeId(group.representative.edgeId)}>
                    <h3>{edge.card?.primaryName || edge.label}</h3>
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

        {activePathway && !selectedPair && !selectedNode && (
          <div className="pathway-result-card live-pathway-card map-draggable-panel" style={panelStyle}>
            <div className="stack-heading pathway-heading map-panel-drag-handle" onPointerDown={handlePanelPointerDown} onPointerMove={handlePanelPointerMove} onPointerUp={finishPanelDrag} onPointerCancel={finishPanelDrag}>
              <div>
                <strong>Pathway result</strong>
                <span>{activePathway.stepCount} steps</span>
              </div>
              <button className="stack-close-button" type="button" onClick={clearPairSelection} title="Close pathway card">
                <X size={18} />
              </button>
            </div>
            <p>{activePathway.summary}</p>
            <div className="pathway-route-list">
              {activePathway.compoundIds.map((compoundId, index) => (
                <span key={`${activePathway.pathwayId}:${compoundId}:${index}`}>{compoundName(compoundId)}</span>
              ))}
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

        <div className="map-footer-stats home-map-stats">
          <span>Total compounds: {graph?.nodes.length ?? 0}</span>
          <span>Total enzyme edges: {graph?.edges.length ?? 0}</span>
          <span>Visible compound pairs: {viewModel.pairs.length}</span>
          <span>Visible map edges: {visibleEdgeCount}</span>
        </div>
      </section>
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
  const scaleX = ((HOME_VIEWBOX_WIDTH - 13) / width) * 0.96
  const scaleY = ((HOME_VIEWBOX_HEIGHT - 18) / height) * 0.96
  const sourceCenter = { x: minX + width / 2, y: minY + height / 2 }
  const targetCenter = { x: HOME_VIEWBOX_WIDTH / 2, y: HOME_VIEWBOX_HEIGHT / 2 }
  const normalized: Record<string, Point> = {}
  Object.entries(positions).forEach(([compoundId, point]) => {
    normalized[compoundId] = {
      x: clamp(targetCenter.x + (point.x - sourceCenter.x) * scaleX, 5.5, HOME_VIEWBOX_WIDTH - 5.5),
      y: clamp(targetCenter.y + (point.y - sourceCenter.y) * scaleY, 7, HOME_VIEWBOX_HEIGHT - 7),
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
      point.x = clamp(point.x, 5.5, HOME_VIEWBOX_WIDTH - 5.5)
      point.y = clamp(point.y, 7, HOME_VIEWBOX_HEIGHT - 7)
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
    const lateral = (slot - (rowItems - 1) / 2) * 9.5
    const depth = 19 + row * 16 + Math.abs(slot - (rowItems - 1) / 2) * 0.8
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



















