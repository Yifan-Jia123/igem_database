export type EntityKind = 'compound' | 'enzyme' | 'reaction'

export type GraphNode = {
  id: string
  label: string
  shortLabel: string
  kind: EntityKind
  x: number
  y: number
  tone: 'teal' | 'amber' | 'coral'
  meta: string
}

export type GraphEdge = {
  id: string
  source: string
  target: string
  label: string
  reactionId: string
  enzymeId: string
  edgeGroupId: string
  curved?: boolean
}

export type Entity = {
  id: string
  kind: EntityKind
  name: string
  subtitle: string
  description: string
  tags: string[]
  fields: Array<{ label: string; value: string }>
  related: Array<{ id: string; name: string; kind: EntityKind }>
  imageLabel?: string
  imageUrl?: string
  species?: string
  compoundClass?: string
  enzymeFamily?: string
}
