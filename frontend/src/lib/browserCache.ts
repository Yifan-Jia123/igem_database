import { useEffect, useState } from 'react'
import type { Entity } from '../types'
import type { BlastSession } from '../api'

const prefix = 'terpene-atlas:v1:'

export const isString = (value: unknown): value is string => typeof value === 'string'
export const isStringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every(isString)
export const isNullableString = (value: unknown): value is string | null => value === null || isString(value)
export const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)

export function isBlastSession(value: unknown): value is BlastSession | null {
  if (value === null) return true
  if (!isRecord(value) || typeof value.id !== 'number' || !isRecord(value.payload)) return false
  const payload = value.payload
  return typeof payload.queryLength === 'number' && typeof payload.searchedSubjects === 'number'
    && typeof payload.threshold === 'number' && Array.isArray(payload.hits)
    && payload.hits.every((hit) => isRecord(hit) && isString(hit.enzymeId)
      && ['canonical', 'isoform'].includes(String(hit.subjectType))
      && ['subjectLength', 'eValue', 'identity', 'queryCover', 'alignmentLength', 'bitscore'].every((key) => typeof hit[key] === 'number')
      && (hit.card == null || (isRecord(hit.card) && isString(hit.card.primaryName))))
}

export function isEntity(value: unknown): value is Entity {
  if (!isRecord(value)) return false
  return isString(value.id) && ['compound', 'enzyme', 'reaction', 'pathway'].includes(String(value.kind))
    && isString(value.name) && isString(value.subtitle) && isString(value.description)
    && isStringArray(value.tags)
    && Array.isArray(value.fields) && value.fields.every((field) => isRecord(field) && isString(field.label) && isString(field.value))
    && Array.isArray(value.related) && value.related.every((item) => isRecord(item) && isString(item.id) && isString(item.name) && ['compound', 'enzyme', 'reaction', 'pathway'].includes(String(item.kind)))
    && (value.pathway === undefined || (isRecord(value.pathway)
      && isString(value.pathway.startId) && isString(value.pathway.endId)
      && isStringArray(value.pathway.compoundIds) && isStringArray(value.pathway.compoundNames)
      && typeof value.pathway.stepCount === 'number'
      && (value.pathway.enzymesByStep === undefined || (Array.isArray(value.pathway.enzymesByStep)
        && value.pathway.enzymesByStep.every((step) => isRecord(step) && typeof step.step === 'number'
          && isString(step.sourceId) && isString(step.sourceName) && isString(step.targetId) && isString(step.targetName)
          && Array.isArray(step.enzymes) && step.enzymes.every((enzyme) => isRecord(enzyme) && isString(enzyme.enzymeId) && isString(enzyme.name)))))))
}

export function readCache<Value>(key: string, fallback: Value, validate: (value: unknown) => value is Value): Value {
  try {
    const stored = localStorage.getItem(prefix + key)
    if (stored !== null) {
      const value: unknown = JSON.parse(stored)
      if (validate(value)) return value
    }
  } catch {
    return fallback
  }
  return fallback
}

export function writeCache(key: string, value: unknown) {
  try {
    localStorage.setItem(prefix + key, JSON.stringify(value))
  } catch {
    console.warn('Browser cache unavailable; changes are kept only for this session.')
  }
}

export function useCachedState<Value>(key: string, fallback: Value, validate: (value: unknown) => value is Value) {
  const [value, setValue] = useState<Value>(() => readCache(key, fallback, validate))
  useEffect(() => {
    writeCache(key, value)
  }, [key, value])
  return [value, setValue] as const
}
