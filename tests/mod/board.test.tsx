import { test, expect, mock } from 'claude-code/testing'

const row = (id: string, column: string, status: string, actions: string[], last = '') =>
  ({ id, name: id, repo: '/r/app', lane: null, status, column, last, report: `/r/app/.sysop/reports/sysop-${id}.review.md`, actions })

const BOARD = {
  tickets: [
    row('t1', 'working', 'running', ['why', 'halt']),
    row('t2', 'working', 'built', ['why', 'halt']),
    row('t3', 'needs_you', 'reviewed', ['land', 'why', 'halt'], '12:00 READY from gemini'),
    row('t4', 'needs_you', 'blocked', ['why', 'halt'], 'needs a product decision'),
    row('t0', 'landed', 'landed', ['why']),
  ],
  repos: ['/r/app'], halted: [], plan: 'Claude plan: 5-hour 41%', plan_percent: 41, spend_usd: 0.12,
  session_model: 'sonnet', next: 'land t3',
}

function world(on: any, board: any = BOARD) {
  const state = { board, runs: [] as string[][], statusCalls: 0, stamp: 1, toasts: [] as string[] }
  const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
  on('process.run', (_: any, e: any) => {
    const argv = e.argv.slice(2)
    if (argv[0] === 'status') return (state.statusCalls++, ok(JSON.stringify(state.board)))
    state.runs.push(argv)
    return ok('landed sysop/t3')
  })
  on('fs.stat', () => ({ value: { kind: 'file', size: 1, mtimeMs: state.stamp, isSymbolicLink: false } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.toast', (_: any, e: any) => (state.toasts.push(e.text ?? e), { value: undefined }))
  on('session.model', () => ({ value: 'claude-sonnet-5-5' }))
  on('ui.render', ($: any, e: any) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  on('session.start', (_: any, e: any) => e)
  on('command.register', (_: any, e: any) => ({ value: { command: e.name } }))
  return state
}

const BAND = { component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100 } } as any
const PANE = { component: 'Pane', requestId: 'sysop-board', props: { title: 'sysop board', isFocused: true, bodyColumns: 100, placement: 'dock' } } as any

test('the band and the board draw on both surfaces, and Land and Why work', async ($, on) => {
  const w = world(on)
  await $.command.run({ command: 'sysop', args: 'board' } as any)
  for (const surface of ['terminal', 'desktop'] as const) {
    const band = await $.ui.mount({ plugin: 'sysop', surface, ...BAND })
    expect((await band.find({ type: 'Text', text: /^sysop/ }))?.text).toBe('sysop · 2 working · 1 needs you · 1 ready · plan 41% · $0.12 today ')
    expect(await band.find({ key: 'land-ready' })).toBeDefined()
    await band.unmount()

    const pane = await $.ui.mount({ plugin: 'sysop', surface, ...PANE })
    expect(await pane.find({ type: 'Text', text: /Needs you \(2\)/ })).toBeDefined()
    expect(await pane.find({ key: 'land:/r/app:t4' })).toBeUndefined()
    await pane.press({ key: 'why:/r/app:t3' })
    expect(await pane.find({ type: 'Text', text: /READY from gemini/ })).toBeDefined()
    await pane.press({ key: 'why:/r/app:t3' })
    expect(await pane.find({ type: 'Text', text: /READY from gemini/ })).toBeUndefined()
    await pane.press({ key: 'land:/r/app:t3' })
    await pane.unmount()
  }
  expect(w.runs).toEqual([['land', '/r/app:sysop/t3', '--land'], ['land', '/r/app:sysop/t3', '--land']])
})

test('the band stays out of the way when sysop has no work', async ($, on) => {
  world(on, { ...BOARD, tickets: [row('t0', 'landed', 'landed', ['why'])] })
  await $.command.run({ command: 'sysop', args: 'board' } as any)
  const band = await $.ui.mount({ plugin: 'sysop', surface: 'terminal', ...BAND })
  expect(await band.find({ type: 'Text', text: /^sysop/ })).toBeUndefined()
})

test('the board refreshes only when a watched record changed, and once a minute', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on)
  await $.session.start({ cwd: '/r/app', surface: 'terminal' } as any)
  const first = w.statusCalls
  await clock.advance(20000)
  expect(w.statusCalls).toBe(first) // four polls, nothing changed: no status run
  w.stamp = 2
  await clock.advance(5000)
  expect(w.statusCalls).toBe(first + 1)
  await clock.advance(40000) // past the minute mark
  expect(w.statusCalls).toBe(first + 2)
})

test('a change worth a glance is one toast', async ($, on) => {
  const w = world(on)
  await $.command.run({ command: 'sysop', args: 'board' } as any)
  w.board = { ...BOARD, tickets: [
    row('t1', 'needs_you', 'blocked', ['why', 'halt'], 'tests fail on main'),
    row('t2', 'needs_you', 'reviewed', ['land', 'why', 'halt']),
    row('t3', 'landed', 'landed', ['why']),
    row('t4', 'needs_you', 'blocked', ['why', 'halt'], 'needs a product decision'),
    row('t0', 'landed', 'landed', ['why']),
  ] }
  await $.command.run({ command: 'sysop', args: 'board' } as any)
  expect(w.toasts).toEqual(['sysop: t1 needs you: tests fail on main', 'sysop: t2 is ready to land', 'sysop: t3 landed'])
})

test('the board lands everything ready in one press', async ($, on) => {
  const w = world(on)
  await $.command.run({ command: 'sysop', args: 'board' } as any)
  const pane = await $.ui.mount({ plugin: 'sysop', surface: 'terminal', ...PANE })
  await pane.press({ key: 'land-all' })
  expect(w.runs).toEqual([['land', '--ready', '--all']])
})
