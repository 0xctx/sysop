import { test, expect, mock } from 'claude-code/testing'

const AGENTS = JSON.stringify({ default: 'sonnet', 'sysop:explorer': 'haiku', 'sysop:simplifier': 'inherit' })

function world(on: any, files: Record<string, number> = {}) {
  const runs: string[][] = []
  on('process.run', (_: any, e: any) => {
    runs.push([...e.argv])
    return { value: { exitCode: 0, stdout: AGENTS, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.root', () => ({ value: '/repo' }))
  on('fs.stat', (_: any, e: any) =>
    e.path in files ? { value: { kind: 'file', size: 1, mtimeMs: files[e.path], isSymbolicLink: false } } : { deny: 'ENOENT' })
  const sent: any[] = []
  on('tool.call', (_: any, e: any) => (sent.push(e), { result: 'done' }))
  return { runs, sent }
}

const call = ($: any, input: any) => $.tool.call({ tool: 'Agent', description: 'x', prompt: 'y', ...input })

test('a sysop agent without a model goes out on the configured one', async ($, on) => {
  mock.env(on, { HOME: '/home/op' })
  const { sent } = world(on)
  await call($, { subagent_type: 'sysop:code-reviewer' })
  await call($, { subagent_type: 'sysop:explorer' })
  expect(sent.map(e => e.model)).toEqual(['sonnet', 'haiku'])
})

test('a given model, inherit, and other agents pass unchanged', async ($, on) => {
  mock.env(on, { HOME: '/home/op' })
  const { sent } = world(on)
  await call($, { subagent_type: 'sysop:code-reviewer', model: 'opus' })
  await call($, { subagent_type: 'sysop:simplifier' })
  await call($, { subagent_type: 'general-purpose' })
  expect(sent.map(e => e.model)).toEqual(['opus', undefined, undefined])
})

test('settings are read once until a settings file changes', async ($, on) => {
  mock.env(on, { HOME: '/home/op' })
  const files: Record<string, number> = { '/home/op/.claude/sysop.json': 1 }
  const { runs } = world(on, files)
  await call($, { subagent_type: 'sysop:code-reviewer' })
  await call($, { subagent_type: 'sysop:code-reviewer' })
  expect(runs.length).toBe(1)
  files['/repo/.claude/sysop.json'] = 2
  await call($, { subagent_type: 'sysop:code-reviewer' })
  expect(runs.length).toBe(2)
  expect(runs[0].slice(2)).toEqual(['settings', '--key', 'agents'])
})
