export type EntityKind = 'compound' | 'enzyme' | 'reaction' | 'pathway'

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

/** One enzyme the user picked (multi-select) to back a single pathway step when
 *  creating an enzyme-picked route from the pathway detail page. ``enzymeId`` is
 *  the genuine library primary key taken verbatim from the backing edge/edge-group
 *  item — never a display-only synthetic id — so a future download can hydrate the
 *  full database record again via POST /search/table/by-ids. The remaining fields
 *  are denormalised display copies only. */
export type PathwayEnzymeChoice = {
  enzymeId: string
  /** UniProt entry (accession) used as the popup card's primary label when the
   *  backing edge/card carries one; falls back to ``name`` for display. */
  uniprotId?: string | null
  name: string
  organismName?: string | null
  sourceType?: string | null
}

/** Per-step chosen enzymes on an enzyme-picked pathway record. ``step`` is 1-based
 *  and matches ``PathwayQueueMeta.stepCount``. Kept per-step + in order so a future
 *  download can map each picked enzyme straight back to its (source→target) step. */
export type PathwayQueueStep = {
  step: number
  sourceId: string
  sourceName: string
  targetId: string
  targetName: string
  enzymes: PathwayEnzymeChoice[]
}

/** Extra payload carried by queued pathway records so the downloads page can
 *  group routes that share the same start/end pair and render each route's
 *  own compound chain. Only present on ``Entity`` objects created for a
 *  pathway search result card (never on dataset compounds/enzymes/reactions). */
export type PathwayQueueMeta = {
  startId: string
  endId: string
  compoundIds: string[]
  /** Human-readable names aligned with ``compoundIds`` (index-for-index), so
   *  the downloads page can draw each route's compound chain without another
   *  lookup. */
  compoundNames: string[]
  stepCount: number
  /** Per-step chosen enzymes, present ONLY on enzyme-picked route variants that
   *  went through the pathway-detail "download" enzyme picker. Plain routes omit
   *  it (DownloadsPage treats ``undefined`` as the plain route and renders no
   *  marker). */
  enzymesByStep?: PathwayQueueStep[]
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
  /** Grouping + chain metadata, present only on records created from pathway
   *  search result cards (see PathwayQueueMeta). */
  pathway?: PathwayQueueMeta
}
