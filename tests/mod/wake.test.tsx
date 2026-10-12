import { test, expect, mock } from 'claude-code/testing'

const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
const EMPTY = { tickets: [], repos: [], halted: [], plan: '', plan_percent: null, spend_usd: 0, session_model: null, next: '' }

function world(on: any) {
  const w = { wake: { wake: false, why: 'only waits are due' } as any, sent: [] as string[], beats: 0 }
  on('process.run', (_: any, e: any) =>
    ok(JSON.stringify(e.argv[2] === 'wake' ? w.wake : EMPTY)))
  on('fs.stat', () => ({ value: { kind: 'file', size: 1, mtimeMs: 1, isSymbolicLink: false } }))
  on('fs.write', (_: any, e: any) => (e.path.includes('/mod-S1.json') && w.beats++, { value: undefined }))
  on('session.id', () => ({ value: 'S1' }))
  on('session.root', () => ({ value: '/r/app' }))
  on('session.model', () => ({ value: 'claude-sonnet-5-5' }))
  on('session.start', (_: any, e: any) => e)
  on('command.register', (_: any, e: any) => ({ value: { command: e.name } }))
  on('prompt.submit', (_: any, e: any) => (w.sent.push(e.text), { text: e.text }))
  on('turn.start', (_: any, e: any) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  return w
}

async function start($: any, on: any) {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/home/op' })
  const w = world(on)
  await $.session.start({ cwd: '/r/app', surface: 'terminal' })
  return { clock, w }
}

test('a finished worker wakes the coordinator once, after the quiet window', async ($, on) => {
  const { clock, w } = await start($, on)
  await clock.advance(30000)
  expect(w.sent).toEqual([]) // only waits are due
  expect(w.beats).toBeGreaterThan(0) // the heartbeat says wakes: true meanwhile
  w.wake = { wake: true, key: ':t1:review', why: 'review t1' }
  await clock.advance(10000) // first seen
  await clock.advance(10000)
  expect(w.sent).toEqual([]) // still inside the 20 s window
  await clock.advance(10000)
  expect(w.sent).toEqual(['sysop: the fleet has work for you (review t1). Resume it: run the sysop:fleet skill with --resume.'])
  await clock.advance(60000)
  expect(w.sent.length).toBe(1) // the same set of due actions never wakes it twice
  w.wake = { wake: true, key: ':t1:land;:t2:review', why: 'land t1, review t2' }
  await clock.advance(40000)
  expect(w.sent.length).toBe(2) // a new set does
})

test('no wake while halted, with no run, with only waits, or while a turn runs', async ($, on) => {
  const { clock, w } = await start($, on)
  w.wake = { wake: false, why: 'halted' }
  await clock.advance(60000)
  w.wake = { wake: false, why: 'no fleet run here' }
  await clock.advance(60000)
  w.wake = { wake: true, key: ':t1:review', why: 'review t1' }
  await $.turn.start({ text: 'hi', turnId: 'x' } as any)
  await clock.advance(60000)
  expect(w.sent).toEqual([])
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 'x', reason: 'answer' } as any)
  await clock.advance(40000)
  expect(w.sent.length).toBe(1)
  expect(w.sent[0]).toContain('sysop:fleet skill with --resume')
})

test('a lane with intake set takes issues in on its timer, and says what joined', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/home/op' })
  const runs: string[][] = []
  const toasts: string[] = []
  on('process.run', (_: any, e: any) => {
    const argv = e.argv.slice(2)
    runs.push(argv)
    if (argv[0] === 'settings') return ok(JSON.stringify({ bg: { preset: 'free-max', intake: { label: 'sysop:bg', every_minutes: 5 } }, fg: { preset: 'claude-plan' } }))
    if (argv[0] === 'lane') return ok(JSON.stringify({ lane: 'bg', added: ['issue-7'] }))
    return ok(JSON.stringify(argv[0] === 'wake' ? { wake: false } : EMPTY))
  })
  on('fs.stat', () => ({ value: { kind: 'file', size: 1, mtimeMs: 1, isSymbolicLink: false } }))
  on('fs.write', () => ({ value: undefined }))
  on('session.id', () => ({ value: 'S1' }))
  on('session.model', () => ({ value: 'claude-sonnet-5-5' }))
  on('session.start', (_: any, e: any) => e)
  on('command.register', (_: any, e: any) => ({ value: { command: e.name } }))
  on('ui.toast', (_: any, e: any) => (toasts.push(e.text ?? e), { value: undefined }))
  await $.session.start({ cwd: '/r/app', surface: 'terminal' } as any)
  await clock.advance(60000)
  const intakes = () => runs.filter(a => a[0] === 'lane').map(a => a.join(' '))
  expect(intakes()).toEqual(['lane intake bg']) // only the lane that set it
  expect(toasts).toContain('sysop: issue-7 joined lane bg')
  await clock.advance(120000)
  expect(intakes().length).toBe(1) // not again before five minutes
  await clock.advance(180000)
  expect(intakes().length).toBe(2)
})
