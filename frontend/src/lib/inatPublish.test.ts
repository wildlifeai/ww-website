import { describe, expect, it } from 'vitest'
import { ApiError } from './apiClient'
import {
  DEPLOYMENT_LOCATION_PATH,
  INAT_CONNECT_PATH,
  INAT_PUBLISH_MAX,
  inatBlocker,
  inatErrorNotice,
  inatPublishNotice,
  type InatPublishAnswer,
} from './inatPublish'
import { isInatOff } from './inatOff'

const answer = (over: Partial<InatPublishAnswer> = {}): InatPublishAnswer => ({
  observations_created: 0,
  photos_uploaded: 0,
  skipped_bycatch: 0,
  skipped_already_published: 0,
  errors: 0,
  observations: [],
  ...over,
})

describe('inatBlocker', () => {
  it('lets a connected user publish a selection from a deployment with a location', () => {
    expect(inatBlocker({ connected: true, hasLocation: true, selected: 3 })).toBeNull()
    expect(inatBlocker({ connected: true, selected: INAT_PUBLISH_MAX })).toBeNull()
  })

  it('sends a user without a connection to Toolkit first', () => {
    const b = inatBlocker({ connected: false, hasLocation: false, selected: 3 })
    expect(b?.text).toMatch(/not connected/)
    expect(b?.link?.to).toBe(INAT_CONNECT_PATH)
  })

  it('asks for the deployment location, then for a smaller selection', () => {
    expect(inatBlocker({ connected: true, hasLocation: false, selected: 3 })?.link?.to).toBe(DEPLOYMENT_LOCATION_PATH)
    expect(inatBlocker({ connected: true, hasLocation: true, selected: INAT_PUBLISH_MAX + 1 })?.text).toMatch(/200 photos or fewer/)
  })
})

describe('inatPublishNotice', () => {
  it('reports what was published, with a link to the first observation', () => {
    const n = inatPublishNotice(answer({
      observations_created: 2,
      photos_uploaded: 5,
      observations: [{ uri: null }, { uri: 'https://www.inaturalist.org/observations/42', photo_errors: 0 }],
    }))
    expect(n).toEqual({
      ok: true,
      text: 'Published 2 observations with 5 photos to iNaturalist.',
      link: { to: 'https://www.inaturalist.org/observations/42', label: 'View on iNaturalist', external: true },
    })
  })

  it('never shows success when nothing was published, and says why', () => {
    const n = inatPublishNotice(answer({ skipped_already_published: 2, skipped_bycatch: 1 }))
    expect(n.ok).toBe(false)
    expect(n.text).toBe('Nothing published. Skipped 3: 2 already on iNaturalist, 1 not an animal or showing a person.')
    expect(n.link).toBeUndefined()
    expect(inatPublishNotice(answer()).text).toBe('Nothing published. None of these photos were found.')
  })

  it('counts failed encounters and photos that did not upload as a failure', () => {
    const n = inatPublishNotice(answer({
      observations_created: 1,
      photos_uploaded: 2,
      errors: 1,
      observations: [{ uri: 'https://www.inaturalist.org/observations/7', photo_errors: 1 }],
    }))
    expect(n.ok).toBe(false)
    expect(n.text).toBe('Published 1 observation with 2 photos to iNaturalist. 1 encounter failed. 1 photo did not upload.')
    expect(inatPublishNotice(answer({ errors: 2 })).text).toBe('Nothing published. 2 encounters failed.')
  })
})

describe('inatErrorNotice', () => {
  it('links a user without a connection to Toolkit', () => {
    const n = inatErrorNotice(new ApiError('UNKNOWN', 'Not connected to iNaturalist'))
    expect(n.ok).toBe(false)
    expect(n.link?.to).toBe(INAT_CONNECT_PATH)
  })

  it('says when iNaturalist is switched off, and passes other refusals through', () => {
    expect(inatErrorNotice(new ApiError('UNKNOWN', 'iNaturalist integration is not enabled')).text)
      .toBe('Not published: iNaturalist is switched off on this server.')
    expect(inatErrorNotice(new ApiError('UNKNOWN', 'You need the Project Member role to change this project.')).text)
      .toBe('Not published: You need the Project Member role to change this project.')
    expect(inatErrorNotice('boom').text).toBe('Not published: boom')
  })
})

describe('isInatOff', () => {
  it('reads the switched-off answer from the message or the status code', () => {
    expect(isInatOff(new ApiError('UNKNOWN', 'iNaturalist integration is not enabled'))).toBe(true)
    expect(isInatOff(new ApiError('HTTP_404', 'Request failed'))).toBe(true)
    expect(isInatOff(new ApiError('NETWORK_ERROR', 'Could not reach the server'))).toBe(false)
  })
})
