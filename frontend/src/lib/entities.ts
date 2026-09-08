import { Beaker, Dna, FlaskConical, Route } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { Entity, EntityKind } from '../types'

export type View = 'home' | 'search' | 'structure' | 'downloads' | 'enzyme'
export type SearchKind = 'all' | EntityKind

export type FilterOptions = {
  species: string[]
  classes: string[]
  families: string[]
}

export type FilterState = {
  query: string
  searchKind: SearchKind
  species: string
  compoundClass: string
  enzymeFamily: string
}

export const kindLabels: Record<EntityKind, string> = {
  compound: 'Compound',
  enzyme: 'Enzyme',
  reaction: 'Reaction',
  pathway: 'Pathway',
}

export const kindIcons: Record<EntityKind, LucideIcon> = {
  compound: Beaker,
  enzyme: Dna,
  reaction: FlaskConical,
  pathway: Route,
}

export function looksLikeProteinSequence(value: string) {
  const compact = value
    .replace(/^>.*$/gm, '')
    .replace(/[^A-Za-z]/g, '')
    .toUpperCase()

  return compact.length >= 30 && /^[ACDEFGHIKLMNPQRSTVWYBXZJUO]+$/.test(compact)
}

export function matchesFilters(entity: Entity | undefined, filters: FilterState, filterOptions: FilterOptions) {
  if (!entity) return false

  if (filters.searchKind !== 'all' && entity.kind !== filters.searchKind) return false
  if (filters.species !== filterOptions.species[0] && entity.species !== filters.species) return false
  if (filters.compoundClass !== filterOptions.classes[0] && entity.compoundClass !== filters.compoundClass) return false
  if (filters.enzymeFamily !== filterOptions.families[0] && entity.enzymeFamily !== filters.enzymeFamily) return false

  const normalizedQuery = filters.query.trim().toLowerCase()
  if (!normalizedQuery) return true

  return [entity.id, entity.name, entity.subtitle, entity.description, ...entity.tags, ...entity.fields.map((field) => `${field.label} ${field.value}`)]
    .join(' ')
    .toLowerCase()
    .includes(normalizedQuery)
}

export function getExternalRecordUrl(entity: Entity) {
  if (entity.kind === 'enzyme') return `https://www.uniprot.org/uniprotkb/${entity.id.replace('ENZ:', '')}`
  if (entity.kind === 'compound') return `https://www.ebi.ac.uk/chebi/searchId.do?chebiId=${entity.id}`
  if (entity.kind === 'pathway') return ''
  return `https://www.rhea-db.org/reaction?id=${entity.id.replace('RHEA:', '')}`
}

export function csvCell(value: string) {
  return `"${value.replace(/"/g, '""')}"`
}
