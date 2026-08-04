import type { Entity, EntityKind, GraphEdge, GraphNode } from './types'

const API_PREFIX = '/api/v1'

type ApiResponse<T> = {
  success: boolean
  data: T
  error?: { code?: string; message?: string }
}

type CompoundCard = {
  compoundId: string
  name: string
  chebiId?: string | null
  formula?: string | null
  charge?: number | null
  averageMass?: number | null
  smiles?: string | null
  inchi?: string | null
  structureImageUrl?: string | null
  chebiUrl?: string | null
  description?: string | null
}

type EnzymeCard = {
  edgeId: string
  enzymeId: string
  primaryName: string
  uniprotId?: string | null
  databaseCode: string
  organismName?: string | null
  ecNumber?: string | null
  reactionId: string
  reactionEquation: string
  reactionDirection: string
  sourceType: string
  reviewStatus: string
}

type ReactionEdge = {
  edgeId: string
  edgeGroupId?: string | null
  reactionId: string
  enzymeId: string
  sourceCompoundId: string
  targetCompoundId: string
  label: string
  direction: string
  sourceType: string
  reviewStatus: string
  card?: EnzymeCard | null
}

type EdgeGroup = {
  edgeGroupId: string
  sourceCompoundId: string
  targetCompoundId: string
  label: string
  count: number
  edgeIds: string[]
}

type GraphPayload = {
  nodes: CompoundCard[]
  edges: ReactionEdge[]
  edgeGroups: EdgeGroup[]
}

type FilterOptionsPayload = {
  organisms?: string[]
  sourceTypes?: string[]
  reviewStatuses?: string[]
}

type HomologyResultItem = {
  enzymeId: string
  eValue: number
  identity?: number | null
  card?: EnzymeCard | null
}

type HomologyPayload = {
  jobId: string
  status: string
  progress?: number | null
  results: HomologyResultItem[]
}

export type ApiDataset = {
  entities: Entity[]
  graphNodes: GraphNode[]
  graphEdges: GraphEdge[]
  filterOptions: {
    species: string[]
    classes: string[]
    families: string[]
  }
}

export type EntrySearchParams = {
  q: string
  organismName?: string
  pageSize?: number
}

export async function loadApiDataset(): Promise<ApiDataset> {
  const [metadata, graph] = await Promise.all([
    request<FilterOptionsPayload>('/metadata/filter-options'),
    request<GraphPayload>('/graph?depth=1&limit_nodes=60'),
  ])

  return adaptDataset(metadata, graph)
}

export async function searchApiEntries({ q, organismName, pageSize = 80 }: EntrySearchParams): Promise<Entity[]> {
  const params = new URLSearchParams({
    q,
    view_mode: 'table',
    page: '1',
    page_size: String(pageSize),
  })
  if (organismName) params.set('organism_name', organismName)

  const payload = await request<{ items: EnzymeCard[] }>(`/search/entries?${params.toString()}`)
  return payload.items.map((enzyme) => enzymeEntity(enzyme))
}

export async function searchHomologyEntries(sequence: string, maxResults = 50): Promise<Entity[]> {
  const payload = await request<HomologyPayload>('/homology/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sequence, maxResults }),
  })

  return payload.results
    .filter((result) => result.card)
    .map((result) => homologyEntity(result))
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, init)
  if (!response.ok) throw new Error(`API ${path} returned ${response.status}`)

  const payload = (await response.json()) as ApiResponse<T>
  if (!payload.success) {
    throw new Error(payload.error?.message || `API ${path} failed`)
  }

  return payload.data
}

function adaptDataset(metadata: FilterOptionsPayload, graph: GraphPayload): ApiDataset {
  const compoundCards = new Map(graph.nodes.map((node) => [node.compoundId, node]))
  const entityMap = new Map<string, Entity>()
  const nodeMap = new Map<string, GraphNode>()
  const graphEdges: GraphEdge[] = []

  graph.nodes.forEach((compound, index) => {
    entityMap.set(compound.compoundId, compoundEntity(compound))
    nodeMap.set(compound.compoundId, compoundNode(compound, index, graph.nodes.length))
  })

  const edges = graph.edges.slice(0, 360)
  edges.forEach((edge, index) => {
    const enzyme = edge.card
    const enzymeId = enzyme?.enzymeId || edge.enzymeId
    const source = compoundCards.get(edge.sourceCompoundId) || fallbackCompound(edge.sourceCompoundId)
    const target = compoundCards.get(edge.targetCompoundId) || fallbackCompound(edge.targetCompoundId)

    if (!entityMap.has(source.compoundId)) entityMap.set(source.compoundId, compoundEntity(source))
    if (!entityMap.has(target.compoundId)) entityMap.set(target.compoundId, compoundEntity(target))
    if (!nodeMap.has(source.compoundId)) nodeMap.set(source.compoundId, compoundNode(source, nodeMap.size, nodeMap.size + 1))
    if (!nodeMap.has(target.compoundId)) nodeMap.set(target.compoundId, compoundNode(target, nodeMap.size, nodeMap.size + 1))

    if (enzyme && !entityMap.has(enzymeId)) {
      entityMap.set(enzymeId, enzymeEntity(enzyme, source, target))
    }
    if (!nodeMap.has(enzymeId)) {
      nodeMap.set(enzymeId, enzymeNode(enzymeId, enzyme, source, target, index, edges.length, nodeMap))
    }

    entityMap.set(edge.reactionId, reactionEntity(edge, source, target, enzyme))

    graphEdges.push({
      id: `${edge.edgeId}:source`,
      source: edge.sourceCompoundId,
      target: enzymeId,
      label: edge.reactionId,
      reactionId: edge.reactionId,
      enzymeId,
      edgeGroupId: edge.edgeGroupId || `${edge.edgeId}:source`,
    })
    graphEdges.push({
      id: `${edge.edgeId}:target`,
      source: enzymeId,
      target: edge.targetCompoundId,
      label: edge.reactionId,
      reactionId: edge.reactionId,
      enzymeId,
      edgeGroupId: edge.edgeGroupId || `${edge.edgeId}:target`,
    })
  })

  graph.edgeGroups.slice(0, 120).forEach((group, index) => {
    const enzymeId = group.edgeGroupId
    if (!nodeMap.has(enzymeId)) {
      nodeMap.set(enzymeId, groupedEnzymeNode(group, index, graph.edgeGroups.length, nodeMap))
    }
    entityMap.set(enzymeId, groupedEntity(group))
    graphEdges.push({
      id: `${group.edgeGroupId}:source`,
      source: group.sourceCompoundId,
      target: enzymeId,
      label: group.label,
      reactionId: group.edgeGroupId,
      enzymeId,
      edgeGroupId: group.edgeGroupId,
      curved: true,
    })
    graphEdges.push({
      id: `${group.edgeGroupId}:target`,
      source: enzymeId,
      target: group.targetCompoundId,
      label: group.label,
      reactionId: group.edgeGroupId,
      enzymeId,
      edgeGroupId: group.edgeGroupId,
      curved: true,
    })
  })

  return {
    entities: Array.from(entityMap.values()),
    graphNodes: Array.from(nodeMap.values()),
    graphEdges,
    filterOptions: {
      species: ['All species', ...(metadata.organisms || [])],
      classes: ['All compound classes'],
      families: ['All enzyme families'],
    },
  }
}

function fallbackCompound(compoundId: string): CompoundCard {
  return {
    compoundId,
    name: compoundId,
    chebiId: compoundId.startsWith('CHEBI:') ? compoundId : null,
    description: 'Referenced compound from the terpene pathway graph.',
  }
}

function compoundEntity(compound: CompoundCard): Entity {
  return {
    id: compound.compoundId,
    kind: 'compound',
    name: compound.name,
    subtitle: compound.chebiId || compound.compoundId,
    description: compound.description || compound.smiles || 'Compound record from the terpene pathway database.',
    tags: ['Compound'],
    imageLabel: compound.structureImageUrl || compound.chebiId ? '2D structure' : undefined,
    imageUrl: compoundImageUrl(compound),
    fields: [
      field('Formula', compound.formula),
      field('Average mass', compound.averageMass),
      field('Charge', compound.charge),
      field('ChEBI', compound.chebiId),
      field('SMILES', compound.smiles),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [],
  }
}

function compoundImageUrl(compound: CompoundCard) {
  const chebiId = compound.chebiId || compound.compoundId
  if (chebiId?.startsWith('CHEBI:')) return `/api/v1/assets/compounds/${encodeURIComponent(chebiId)}/structure.svg?v=4`
  return compound.structureImageUrl || undefined
}

function enzymeEntity(enzyme: EnzymeCard, source?: CompoundCard, target?: CompoundCard): Entity {
  return {
    id: enzyme.enzymeId,
    kind: 'enzyme',
    name: enzyme.primaryName,
    subtitle: enzyme.uniprotId || enzyme.databaseCode,
    description: enzyme.reactionEquation || 'Enzyme record from the terpene pathway database.',
    tags: [enzyme.sourceType, enzyme.reviewStatus].filter(Boolean),
    species: enzyme.organismName || undefined,
    fields: [
      field('UniProt', enzyme.uniprotId),
      field('EC number', enzyme.ecNumber),
      field('Organism', enzyme.organismName),
      field('Reaction', enzyme.reactionId),
      field('Direction', enzyme.reactionDirection),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [
      related(source?.compoundId, source?.name, 'compound'),
      related(target?.compoundId, target?.name, 'compound'),
    ].filter(Boolean) as Array<{ id: string; name: string; kind: EntityKind }>,
  }
}

function homologyEntity(result: HomologyResultItem): Entity {
  const entity = enzymeEntity(result.card!)
  entity.tags = ['Homology hit', ...entity.tags]
  entity.fields = [
    field('Identity', result.identity === null || result.identity === undefined ? null : `${result.identity}%`),
    field('E-value', result.eValue.toExponential(2)),
    ...entity.fields,
  ].filter(Boolean) as Array<{ label: string; value: string }>
  return entity
}

function reactionEntity(edge: ReactionEdge, source?: CompoundCard, target?: CompoundCard, enzyme?: EnzymeCard | null): Entity {
  return {
    id: edge.reactionId,
    kind: 'reaction',
    name: edge.reactionId,
    subtitle: enzyme?.ecNumber || edge.direction,
    description: enzyme?.reactionEquation || `${source?.name || edge.sourceCompoundId} -> ${target?.name || edge.targetCompoundId}`,
    tags: [edge.sourceType, edge.reviewStatus].filter(Boolean),
    fields: [
      field('Reaction ID', edge.reactionId),
      field('Direction', edge.direction),
      field('Source type', edge.sourceType),
      field('Review status', edge.reviewStatus),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [
      related(source?.compoundId, source?.name, 'compound'),
      related(enzyme?.enzymeId, enzyme?.primaryName, 'enzyme'),
      related(target?.compoundId, target?.name, 'compound'),
    ].filter(Boolean) as Array<{ id: string; name: string; kind: EntityKind }>,
  }
}

function groupedEntity(group: EdgeGroup): Entity {
  return {
    id: group.edgeGroupId,
    kind: 'enzyme',
    name: group.label,
    subtitle: `${group.count} enzyme links`,
    description: 'Multiple enzyme-reaction links connect this compound pair.',
    tags: ['Grouped edge'],
    fields: [
      field('Edge group', group.edgeGroupId),
      field('Count', group.count),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [],
  }
}

function compoundNode(compound: CompoundCard, index: number, total: number): GraphNode {
  const columns = Math.max(3, Math.ceil(Math.sqrt(Math.max(total, 1))))
  const row = Math.floor(index / columns)
  const col = index % columns
  return {
    id: compound.compoundId,
    label: compound.name,
    shortLabel: shortLabel(compound.name, compound.chebiId || compound.compoundId),
    kind: 'compound',
    x: 10 + col * (80 / Math.max(columns - 1, 1)),
    y: 18 + row * 24,
    tone: index === 0 ? 'teal' : 'coral',
    meta: compound.chebiId || compound.compoundId,
  }
}

function enzymeNode(
  enzymeId: string,
  enzyme: EnzymeCard | null | undefined,
  source: CompoundCard | undefined,
  target: CompoundCard | undefined,
  index: number,
  total: number,
  nodeMap: Map<string, GraphNode>,
): GraphNode {
  const sourceNode = source ? nodeMap.get(source.compoundId) : undefined
  const targetNode = target ? nodeMap.get(target.compoundId) : undefined
  return {
    id: enzymeId,
    label: enzyme?.primaryName || enzymeId,
    shortLabel: shortLabel(enzyme?.primaryName || enzymeId, enzyme?.uniprotId || enzymeId),
    kind: 'enzyme',
    x: sourceNode && targetNode ? (sourceNode.x + targetNode.x) / 2 : 18 + (index % Math.max(total, 1)) * 7,
    y: sourceNode && targetNode ? (sourceNode.y + targetNode.y) / 2 - 8 : 50,
    tone: 'amber',
    meta: enzyme?.ecNumber || enzyme?.uniprotId || enzymeId,
  }
}

function groupedEnzymeNode(group: EdgeGroup, index: number, total: number, nodeMap: Map<string, GraphNode>): GraphNode {
  const sourceNode = nodeMap.get(group.sourceCompoundId)
  const targetNode = nodeMap.get(group.targetCompoundId)
  return {
    id: group.edgeGroupId,
    label: group.label,
    shortLabel: `${group.count}x`,
    kind: 'enzyme',
    x: sourceNode && targetNode ? (sourceNode.x + targetNode.x) / 2 : 18 + (index % Math.max(total, 1)) * 7,
    y: sourceNode && targetNode ? (sourceNode.y + targetNode.y) / 2 - 8 : 50,
    tone: 'amber',
    meta: group.edgeGroupId,
  }
}

function field(label: string, rawValue: string | number | null | undefined) {
  if (rawValue === null || rawValue === undefined || rawValue === '') return null
  return { label, value: String(rawValue) }
}

function related(id: string | undefined, name: string | undefined, kind: EntityKind) {
  if (!id || !name) return null
  return { id, name, kind }
}

function shortLabel(name: string, fallback: string) {
  const cleanName = name.replace(/[^A-Za-z0-9\s-]/g, '').trim()
  const words = cleanName.split(/\s+/).filter(Boolean)
  if (words.length >= 2) return words.slice(0, 2).map((word) => word[0]).join('').toUpperCase()
  if (words[0]) return words[0].slice(0, 4)
  return fallback.replace(/^.*:/, '').slice(0, 4)
}

export type HomeGraphCompound = {
  compoundId: string
  name: string
  chebiId?: string | null
  formula?: string | null
  charge?: number | null
  averageMass?: number | null
  smiles?: string | null
  inchi?: string | null
  structureImageUrl?: string | null
  chebiUrl?: string | null
  description?: string | null
}

export type HomeGraphEnzymeCard = {
  edgeId: string
  enzymeId: string
  primaryName: string
  uniprotId?: string | null
  databaseCode: string
  organismName?: string | null
  ecNumber?: string | null
  reactionId: string
  reactionEquation: string
  reactionDirection: string
  sourceType: string
  reviewStatus: string
}

export type HomeGraphEdge = {
  edgeId: string
  edgeGroupId?: string | null
  reactionId: string
  enzymeId: string
  sourceCompoundId: string
  targetCompoundId: string
  label: string
  direction: string
  sourceType: string
  reviewStatus: string
  card?: HomeGraphEnzymeCard | null
}

export type HomeGraphEdgeGroup = {
  edgeGroupId: string
  sourceCompoundId: string
  targetCompoundId: string
  label: string
  count: number
  edgeIds: string[]
}

export type HomeGraphData = {
  nodes: HomeGraphCompound[]
  edges: HomeGraphEdge[]
  edgeGroups: HomeGraphEdgeGroup[]
}

export type EnzymeGeneDetail = {
  geneName?: string | null
  geneRecordId?: string | null
  genbankId?: string | null
  ncbiUrl?: string | null
  enaAccession?: string | null
  proteinAccession?: string | null
}

export type EnzymeSequenceLink = {
  category: string
  accession: string
  url?: string | null
  relatedAccession?: string | null
  relatedUrl?: string | null
}

export type EnzymeEvidenceDetail = {
  doi?: string | null
  pubmedId?: string | null
  sourceDescription?: string | null
  reviewStatus?: string | null
}

export type EnzymeReactionDetail = {
  reactionId: string
  rheaId?: string | null
  rheaUrl?: string | null
  equation: string
  direction: string
  ecNumber?: string | null
  smiles?: string | null
  atomMapImageUrl?: string | null
  substrates: HomeGraphCompound[]
  products: HomeGraphCompound[]
  sourceType: string
  reviewStatus: string
}

export type EnzymeDetailData = {
  enzymeId: string
  databaseCode: string
  primaryName: string
  secondaryNames: string[]
  uniprotId?: string | null
  uniprotUrl?: string | null
  organismName?: string | null
  sequence?: string | null
  length?: number | null
  mass?: number | null
  gene?: EnzymeGeneDetail | null
  sequenceLinks: EnzymeSequenceLink[]
  reactions: EnzymeReactionDetail[]
  evidence: EnzymeEvidenceDetail[]
  links: Array<{ label: string; url: string }>
}

export type HomeGraphRequest = {
  centerCompoundId?: string
  depth?: number
  limitNodes?: number
  selectionMode?: 'global'
}

export type HomePathwayCard = {
  pathwayId: string
  summary: string
  compoundIds: string[]
  edgeIds: string[]
  edgeGroupIds: string[]
  stepCount: number
  score?: number | null
  graph?: HomeGraphData | null
}

export async function loadHomeGraph(options: HomeGraphRequest = {}): Promise<HomeGraphData> {
  const params = new URLSearchParams({
    depth: String(options.depth ?? 1),
    limit_nodes: String(options.limitNodes ?? (options.centerCompoundId ? 42 : 120)),
  })
  if (options.centerCompoundId) params.set('center_compound_id', options.centerCompoundId)
  if (!options.centerCompoundId) params.set('selection_mode', options.selectionMode ?? 'global')
  return request<HomeGraphData>(`/graph?${params.toString()}`)
}

export async function searchHomePathways(startCompoundId: string, endCompoundId: string, maxSteps = 6): Promise<HomePathwayCard[]> {
  const payload = await request<{ items: HomePathwayCard[] }>('/search/pathways', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      startCompoundId,
      endCompoundId,
      maxSteps,
      limit: 3,
    }),
  })
  return payload.items
}

export async function loadEnzymeDetail(enzymeId: string): Promise<EnzymeDetailData> {
  return request<EnzymeDetailData>(`/enzymes/${encodeURIComponent(enzymeId)}`)
}

export async function loadExpandedEdgeGroup(edgeGroupId: string): Promise<HomeGraphEdge[]> {
  const payload = await request<{ edgeGroupId: string; edges: HomeGraphEdge[] }>(`/graph/edge-groups/${encodeURIComponent(edgeGroupId)}/edges`)
  return payload.edges
}

export async function createEnzymeDownload(enzymeId: string, label: string): Promise<{ fileUrl?: string | null; status: string }> {
  const payload = await request<{ fileUrl?: string | null; status: string }>('/download/files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      downloadType: 'enzyme',
      items: [
        {
          entityType: 'enzyme',
          entityId: enzymeId,
          displayLabel: label,
        },
      ],
      fields: [
        'primaryName',
        'databaseCode',
        'uniprotId',
        'organismName',
        'ecNumber',
        'reactionEquation',
        'direction',
        'smiles',
        'geneName',
        'genbankId',
        'doi',
        'pubmedId',
      ],
      format: 'csv',
      includeExternalLinks: true,
      includeGraphImage: false,
    }),
  })

  return payload
}
