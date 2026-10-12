import { test, expect } from 'claude-code/testing'

const ok = (stdout: string, exitCode = 0, stderr = '') =>
  ({ value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false } }) as any

async function run($: any, on: any, args: string, answer = ok('fine')) {
  const calls: string[][] = []
  on('process.run', (_: any, e: any) => {
    calls.push([...e.argv])
    return answer
  })
  const { text } = await $.command.run({ command: 'sysop', args })
  return { text, argv: calls[0]?.slice(2) }
}

const CASES: [string, string[]][] = [
  ['', ['status', '--all']],
  ['land t1', ['land', 'sysop/t1', '--land']],
  ['land api:t2', ['land', 'api:sysop/t2', '--land']],
  ['halt', ['halt']],
  ['halt deploy freeze', ['halt', '--reason', 'deploy freeze']],
  ['unhalt', ['unhalt']],
  ['preset free-max', ['preset', 'use', 'free-max']],
  ['preset back', ['preset', 'use', 'previous']],
  ['preset', ['preset', 'list']],
  ['lane', ['lane', 'list']],
  ['lane add bg --preset free-max', ['lane', 'add', 'bg', '--preset', 'free-max']],
  ['doctor', ['doctor']],
  ['doctor --probe', ['doctor', '--probe']],
  ['land', ['land', '--ready', '--all']],
  ['autoland on', ['config', 'set', 'land', 'true', '--scope', 'local']],
  ['autoland off', ['config', 'set', 'land', 'false', '--scope', 'local']],
  ['serve up', ['serve', 'up']],
  ['models', ['models']],
  ['preset show free-max', ['preset', 'show', 'free-max']],
  ['lane intake bg', ['lane', 'intake', 'bg']],
]

for (const [args, argv] of CASES) {
  test(`/sysop ${args || '(no args)'} runs sysop.py ${argv.join(' ')}`, async ($, on) => {
    const got = await run($, on, args)
    expect(got.argv).toEqual(argv)
    expect(got.text).toBe('fine')
  })
}

test('the script is the plugin\'s own sysop.py', async ($, on) => {
  const calls: string[][] = []
  on('process.run', (_: any, e: any) => (calls.push([...e.argv]), ok('x')))
  await $.command.run({ command: 'sysop', args: '' } as any)
  expect(calls[0][0]).toBe('python3')
  expect(calls[0][1]).toMatch(/\/scripts\/sysop\.py$/)
})

test('a refusal prints the gate\'s reason, not the log', async ($, on) => {
  const got = await run($, on, 'land t1', ok('', 1, 'sysop: refusing to land sysop/t1: no review report\n'))
  expect(got.text).toBe('refusing to land sysop/t1: no review report')
})

test('help and usage mistakes answer without running anything', async ($, on) => {
  let ran = 0
  on('process.run', () => (ran++, ok('x')))
  for (const args of ['help', 'land a b', 'autoland maybe', 'unhalt now', 'frobnicate', 'launch t1']) {
    const { text } = await $.command.run({ command: 'sysop', args } as any)
    expect(text).toMatch(/\/sysop/)
  }
  expect(ran).toBe(0)
})

test('long output is cut with a pointer', async ($, on) => {
  const got = await run($, on, '', ok('x'.repeat(9000)))
  expect(got.text.length).toBeLessThan(4200)
  expect(got.text).toContain('cut')
})

test('/sysop demo runs the demo script from the plugin', async ($, on) => {
  const calls: string[][] = []
  on('process.run', (_: any, e: any) => (calls.push([...e.argv]), ok('Demo ready')))
  const { text } = await $.command.run({ command: 'sysop', args: 'demo /tmp/d' } as any)
  expect(calls[0][0]).toBe('bash')
  expect(calls[0][1]).toMatch(/\/scripts\/demo\.sh$/)
  expect(calls[0].slice(2)).toEqual(['/tmp/d'])
  expect(text).toBe('Demo ready')
})

