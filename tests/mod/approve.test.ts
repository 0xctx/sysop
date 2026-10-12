import { test, expect, mock } from 'claude-code/testing'

const DENY = (reason: string) => ({ decision: 'deny', hook: 'PreToolUse',
  reason: `sysop guard blocked: ${reason}. Do not retry this in another form. Stop and ask the operator.` })

function world(on: any, verdict: any, answer: string | null) {
  const w = { asked: [] as string[], written: '' }
  on('tool.check', () => verdict)
  on('tool.call', { tool: 'AskUserQuestion' }, (_: any, e: any) => {
    w.asked.push(JSON.stringify(e))
    if (answer === null) return { deny: 'no one to ask' }
    return { result: { answers: { [e.questions?.[0]?.question ?? 'q']: answer } } }
  })
  on('session.root', () => ({ value: '/r/app' }))
  on('session.id', () => ({ value: 'S1' }))
  on('fs.read', () => ({ deny: 'ENOENT' }))
  on('fs.write', (_: any, e: any) => ((w.written = e.text), { value: undefined }))
  return w
}

const check = ($: any, command: string) => $.tool.check({ tool: 'Bash', input: { command } })

for (const [reason, command] of [
  ['git reset --hard discards work', 'git reset --hard origin/main'],
  ['forced branch deletion loses unmerged work (use -d)', 'git branch -D old'],
  ['force push (needs the operator\'s word)', 'git push -f origin main'],
  ['git clean -f deletes untracked files', 'git clean -fd'],
] as const) {
  test(`offered: ${reason}`, async ($, on) => {
    mock.clock(on)
    const w = world(on, DENY(reason), 'Run once')
    const got = await check($, command)
    expect(got.decision).toBe('allow')
    expect(w.asked.length).toBe(1)
    expect(JSON.parse(w.written.trim()).command).toBe(command)
  })
}

for (const reason of ['reading a credential file', 'writing sysop\'s own records by hand is forging them',
  'the commit message credits an AI']) {
  test(`never offered: ${reason}`, async ($, on) => {
    const w = world(on, DENY(reason), 'Run once')
    expect((await check($, 'cat ~/.ssh/id_rsa')).decision).toBe('deny')
    expect(w.asked.length).toBe(0)
  })
}

test('refused, or nobody to ask (headless): the guard stands', async ($, on) => {
  const w = world(on, DENY('git reset --hard discards work'), 'Refuse')
  expect((await check($, 'git reset --hard')).decision).toBe('deny')
  expect(w.written).toBe('')
})

test('headless: the dialog cannot open and the guard stands', async ($, on) => {
  const w = world(on, DENY('git reset --hard discards work'), null)
  expect((await check($, 'git reset --hard')).decision).toBe('deny')
  expect(w.written).toBe('')
})
