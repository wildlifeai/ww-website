import { describe, expect, it } from 'vitest'
import {
  buildLocationPayload, coordinatesMoved, formFromLocation, parseOptionalNumber, validateLocation,
  type DeploymentLocation, type LocationForm,
} from './deploymentLocation'

const stored: DeploymentLocation = {
  id: 'dep-1',
  location_name: 'Ridge track',
  location_description: null,
  latitude: -41.29,
  longitude: 174.78,
  altitude: 120,
  accuracy: 8,
  timezone: 'Pacific/Auckland',
}

const form = (patch: Partial<LocationForm> = {}): LocationForm => ({ ...formFromLocation(stored), ...patch })

describe('parseOptionalNumber', () => {
  it('reads blank as null and junk as NaN', () => {
    expect(parseOptionalNumber('  ')).toBeNull()
    expect(parseOptionalNumber(' -41.5 ')).toBe(-41.5)
    expect(parseOptionalNumber('abc')).toBeNaN()
    expect(parseOptionalNumber('Infinity')).toBeNaN()
  })
})

describe('formFromLocation', () => {
  it('shows nulls as empty fields', () => {
    expect(form()).toEqual({
      name: 'Ridge track', description: '', latitude: '-41.29', longitude: '174.78', altitude: '120', accuracy: '8',
    })
  })
})

describe('validateLocation', () => {
  it('accepts the stored location', () => {
    expect(validateLocation(form())).toEqual({})
  })

  it('requires a name', () => {
    expect(validateLocation(form({ name: '   ' })).name).toBeTruthy()
  })

  it('keeps coordinates in range', () => {
    expect(validateLocation(form({ latitude: '90.1' })).latitude).toBeTruthy()
    expect(validateLocation(form({ longitude: '-180.5' })).longitude).toBeTruthy()
    expect(validateLocation(form({ latitude: '-90', longitude: '180' }))).toEqual({})
  })

  it('wants both coordinates or neither', () => {
    expect(validateLocation(form({ longitude: '' })).longitude).toMatch(/both/)
    expect(validateLocation(form({ latitude: '' })).latitude).toMatch(/both/)
    expect(validateLocation(form({ latitude: '', longitude: '' }))).toEqual({})
  })

  it('rejects text in number fields and a negative accuracy', () => {
    expect(validateLocation(form({ latitude: '41S' })).latitude).toBeTruthy()
    expect(validateLocation(form({ altitude: 'high' })).altitude).toBeTruthy()
    expect(validateLocation(form({ accuracy: '-1' })).accuracy).toBeTruthy()
    expect(validateLocation(form({ altitude: '-3' }))).toEqual({})
  })
})

describe('coordinatesMoved', () => {
  it('compares numbers, not text', () => {
    expect(coordinatesMoved(form({ latitude: '-41.290' }), stored)).toBe(false)
    expect(coordinatesMoved(form({ latitude: '-41.3' }), stored)).toBe(true)
    expect(coordinatesMoved(form({ latitude: '', longitude: '' }), stored)).toBe(true)
  })
})

describe('buildLocationPayload', () => {
  it('trims text and sends blanks as null', () => {
    expect(buildLocationPayload(form({ name: ' Ridge ', description: '  ', altitude: '' }), stored)).toEqual({
      location_name: 'Ridge', location_description: null, latitude: -41.29, longitude: 174.78, altitude: null, accuracy: 8,
    })
  })

  it('keeps the accuracy when the point stays put', () => {
    expect(buildLocationPayload(form({ name: 'Renamed' }), stored).accuracy).toBe(8)
  })

  it('clears the old accuracy when the point moves', () => {
    expect(buildLocationPayload(form({ latitude: '-41.3' }), stored).accuracy).toBeNull()
  })

  it('keeps a new accuracy given with the new point', () => {
    expect(buildLocationPayload(form({ latitude: '-41.3', accuracy: '4' }), stored).accuracy).toBe(4)
  })
})
