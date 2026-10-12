import { test, expect, mock } from 'claude-code/testing'

const LIMITS = [
  { kind: 'five_hour', percentUsed: 84, resetsAt: '2026-10-12T01:30:00.000Z' },
  { kind: 'seven_day', percentUsed: 6, resetsAt: '2026-10-18T08:00:00.000Z' },
]

function files(on: any) {
  const written: Record<string, string> = {}
  on('fs.write', (_: any, e: any) => ((written[e.path] = e.text), { value: undefined }))
  on('session.measure', (_: any, e: any) => ({ changed: e.changed }))
  return written
}

test('a plan reading is recorded where sysop.py reads it', async ($, on) => {
  mock.env(on, { HOME: '/home/op' })
  const clock = mock.clock(on)
  await clock.set(1_800_000_000_000)
  const written = files(on)
  await $.session.measure({ context: { window: 1 }, rateLimits: LIMITS, changed: ['rateLimits'] } as any)
  const record = JSON.parse(written['/home/op/.cache/sysop/claude-usage.json'])
  expect(record.rateLimits).toEqual(LIMITS)
  expect(record.at).toBe(1_800_000_000)
})

test('XDG_CACHE_HOME is honoured, and an empty or unchanged reading writes nothing', async ($, on) => {
  mock.env(on, { HOME: '/home/op', XDG_CACHE_HOME: '/xdg' })
  mock.clock(on)
  const written = files(on)
  await $.session.measure({ context: { window: 1 }, rateLimits: [], changed: ['rateLimits'] } as any)
  await $.session.measure({ context: { window: 1 }, rateLimits: LIMITS, changed: ['context'] } as any)
  expect(Object.keys(written)).toEqual([])
  await $.session.measure({ context: { window: 1 }, rateLimits: LIMITS, changed: ['rateLimits'] } as any)
  expect(Object.keys(written)).toEqual(['/xdg/sysop/claude-usage.json'])
})
