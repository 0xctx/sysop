import { atom, read, update } from 'claude-code'

// The sysop mod: what runs inside Claude Code. It shows, wakes and runs the exact `sysop.py` command the
// operator pressed; it never decides. Mechanics live in scripts/ (tested Python), judgement in skills.

const HELP = [
  '/sysop                        the board as text: every lane in every repo',
  '/sysop board                  the board as a pane, with Land, Why and Halt buttons',
  '/sysop land                   merge everything that is ready (your word; the gates still apply)',
  '/sysop land <ticket>          merge one ready ticket',
  '/sysop autoland on|off        this repo merges ready work by itself, or waits for /sysop land',
  '/sysop halt [reason]          stop new work; /sysop unhalt resumes',
  '/sysop preset [name|back]     list, switch or undo model presets',
  '/sysop lane …                 lanes: list, add <name> --preset <p> [--land] [--pr], intake <lane>',
  '/sysop models · config · serve · kaggle · doctor    the same commands as sysop.py, no tokens',
  '/sysop demo [folder]          three small repos with work queued: then /sysop:fleet --all --yes',
  'Asking for work is /sysop:go.',
].join('\n')

const MAX_TEXT = 4000
// The everyday sysop.py commands, passed through as typed (plumbing stays with the skills).
const PASS = new Set(['models', 'config', 'serve', 'kaggle', 'doctor', 'lane'])

// The argv after `sysop.py` for what the operator typed, or a string to print instead.
export function plan(args) {
  const words = args.trim().split(/\s+/).filter(Boolean)
  const [verb = 'status', ...rest] = words
  switch (verb) {
    case 'status':
      return ['status', '--all', ...rest]
    case 'board':
      return { pane: BOARD }
    case 'land': {
      if (rest.length === 0) return ['land', '--ready', '--all']
      if (rest.length !== 1) return 'usage: /sysop land            (everything ready)\n       /sysop land <ticket>   (or <repo>:<ticket>)'
      const [repo, ticket] = rest[0].includes(':') ? rest[0].split(/:(.*)/s) : ['', rest[0]]
      const branch = ticket.startsWith('sysop/') ? ticket : `sysop/${ticket}`
      return ['land', repo ? `${repo}:${branch}` : branch, '--land']
    }
    case 'autoland':
      if (rest.length !== 1 || !['on', 'off'].includes(rest[0])) return 'usage: /sysop autoland on|off'
      return ['config', 'set', 'land', rest[0] === 'on' ? 'true' : 'false', '--scope', 'local']
    case 'halt':
      return rest.length ? ['halt', '--reason', rest.join(' ')] : ['halt']
    case 'unhalt':
      return rest.length ? 'usage: /sysop unhalt' : ['unhalt']
    case 'preset':
      if (rest.length === 0) return ['preset', 'list']
      if (rest.length === 1) return ['preset', 'use', rest[0] === 'back' ? 'previous' : rest[0]]
      return ['preset', ...rest]
    case 'demo':  // three small repos with work queued in one lane: then /sysop:fleet --all --yes
      return { script: 'demo.sh', args: rest }
    case 'help':
      return HELP
    default:
      if (PASS.has(verb)) return verb === 'lane' && rest.length === 0 ? ['lane', 'list'] : [verb, ...rest]
      return `/sysop has no "${verb}".\n${HELP}`
  }
}

// `land` with no ticket answers JSON: said as one line per outcome.
export function landed(text) {
  try {
    const out = JSON.parse(text)
    if (!Array.isArray(out.landed)) return text
    const lines = out.landed.length ? [`Landed: ${out.landed.join(', ')}`] : []
    if (out.refused) lines.push(`Stopped at ${out.refused.ticket}: ${out.refused.why}`)
    return lines.join('\n') || 'Nothing was ready to land.'
  } catch {
    return text
  }
}

// What the operator reads: the command's output, or its refusal; never a log dump.
export function say(result) {
  const out = result.exitCode === 0 ? result.stdout : result.stderr.trim() || result.stdout
  const text = out.trim().replace(/^sysop: /, '') || (result.exitCode === 0 ? 'done' : `failed (exit ${result.exitCode})`)
  return text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}\n… (cut; run the command in a shell for all of it)` : text
}

// Settings `agents` (the model each sysop agent runs on), read through sysop.py and kept until a settings file
// changes, so a reviewer dispatched without its model goes out with it instead of bouncing off the guard.
let agentsCache = { key: '', agents: {} }

async function agentModels($) {
  const [home, root] = await Promise.all([$.env.get('HOME'), $.session.root()])
  const stamps = await Promise.all([`${home}/.claude/sysop.json`, `${root}/.claude/sysop.json`].map(path =>
    $.fs.stat(path).then(s => s.mtimeMs, () => 0)))
  const key = `${root}:${stamps.join(':')}`
  if (key !== agentsCache.key) {
    const out = await $.process.run(['python3', `${$.plugin.root}/scripts/sysop.py`, 'settings', '--key', 'agents'])
    let agents = {}
    try {
      agents = (out.exitCode === 0 && JSON.parse(out.stdout)) || {}
    } catch {} // unreadable: no model filled in; the guard still asks for it
    agentsCache = { key, agents }
  }
  return agentsCache.agents
}

async function withAgentModel($, e, next) {
  const agent = String(e.subagent_type || '')
  if (!agent.startsWith('sysop:') || e.model) return next(e)
  const agents = await agentModels($)
  const want = agents[agent] || agents.default
  return want && want !== 'inherit' ? next({ ...e, model: want }) : next(e)
}

// The Claude plan's limits, for sysop.py: route keeps delegated work off Claude near a limit (claude_reserve).
// Written only from a real reading: before the first one, rateLimits is empty and means nothing.
async function cacheDir($) {
  const [xdg, home] = await Promise.all([$.env.get('XDG_CACHE_HOME'), $.env.get('HOME')])
  return xdg ? `${xdg}/sysop` : home ? `${home}/.cache/sysop` : null // null: nowhere sysop.py would look
}

async function recordUsage($, rateLimits) {
  if (!rateLimits || rateLimits.length === 0) return
  const dir = await cacheDir($)
  if (!dir) return
  const record = { rateLimits: rateLimits.map(({ kind, percentUsed, resetsAt }) => ({ kind, percentUsed, resetsAt })),
                   at: (await $.clock.now()) / 1000 }
  await $.fs.write(`${dir}/claude-usage.json`, JSON.stringify(record))
}

// --- The board: a band above the prompt while sysop has work, and a pane with a button per action. ---------
// Both draw `status --all --json`, refreshed only when a watched record changed (5 s stat) or once a minute.

const BOARD = 'sysop-board'
const board = atom({ plugin: 'sysop', key: 'board' }, null)
const why = atom({ plugin: 'sysop', key: 'why' }, null)
const TIERS = ['haiku', 'sonnet', 'opus']
let seen = '' // the watched records' stamps at the last refresh
let hinted = false // the session-model hint shows once per session

const sysopArgv = ($, argv) => ['python3', `${$.plugin.root}/scripts/sysop.py`, ...argv]

export function counts(rows) {
  const ready = rows.filter(r => r.actions.includes('land') || r.column === 'ready').length
  const needs = rows.filter(r => r.column === 'needs_you' && !r.actions.includes('land')).length
  const working = rows.filter(r => r.column === 'working').length
  return { working, needs, ready }
}

export function bandText(b) {
  const { working, needs, ready } = counts(b.tickets)
  const parts = ['sysop']
  if (working) parts.push(`${working} working`)
  if (needs) parts.push(`${needs} need${needs === 1 ? 's' : ''} you`)
  if (ready) parts.push(`${ready} ready`)
  if (b.halted.length) parts.push('halted')
  if (b.plan_percent != null) parts.push(`plan ${b.plan_percent}%`)
  if (b.spend_usd) parts.push(`$${b.spend_usd.toFixed(2)} today`)
  return parts.join(' · ')
}

// One line per change worth a glance: landed, blocked, or waiting for your word.
export function transitions(before, after) {
  const was = new Map(before.map(r => [`${r.repo}:${r.id}`, r]))
  const lines = []
  for (const r of after) {
    const old = was.get(`${r.repo}:${r.id}`)
    if (!old || (old.column === r.column && old.status === r.status)) continue
    if (r.column === 'landed') lines.push(`sysop: ${r.name} landed`)
    else if (r.actions.includes('land') && !old.actions.includes('land')) lines.push(`sysop: ${r.name} is ready to land`)
    else if (r.status === 'blocked') lines.push(`sysop: ${r.name} needs you: ${r.last}`)
  }
  return lines
}

async function stamps($) {
  const b = await read($, board)
  const paths = (b?.repos ?? []).flatMap(repo => ['tickets.json', 'HALT', 'active', 'runs'].map(f => `${repo}/.sysop/${f}`))
  const marks = await Promise.all(paths.map(p => $.fs.stat(p).then(s => s.mtimeMs, () => 0)))
  return marks.join(',')
}

async function refresh($) {
  const out = await $.process.run(sysopArgv($, ['status', '--all', '--json']), { timeoutMs: 60000 })
  if (out.exitCode !== 0) return
  let next
  try {
    next = JSON.parse(out.stdout)
  } catch {
    return
  }
  const before = await read($, board)
  if (before) for (const line of transitions(before.tickets, next.tickets)) $.ui.toast(line)
  await update($, board, () => next)
  seen = await stamps($)
  if (!hinted && next.session_model) {
    hinted = true
    const model = String(await $.session.model()).toLowerCase()
    const have = TIERS.findIndex(t => model.includes(t))
    const want = TIERS.indexOf(next.session_model)
    if (have > want && want >= 0) {
      $.ui.toast(`sysop: this session runs on ${TIERS[have]}; the preset suggests ${next.session_model} (/model)`)
    }
  }
}

async function poll($) {
  if ((await stamps($)) !== seen) await refresh($)
}

async function press($, argv) {
  const result = await $.process.run(sysopArgv($, argv), { timeoutMs: 600000 })
  $.ui.toast(`sysop: ${say(result).split('\n')[0]}`)
  await refresh($)
}

async function landReady($) {
  const result = await $.process.run(sysopArgv($, ['land', '--ready', '--all']), { timeoutMs: 600000 })
  for (const line of landed(result.stdout || result.stderr).split('\n')) $.ui.toast(`sysop: ${line}`)
  await refresh($)
}

function band($, e, b) {
  const { Box, Button, Text } = $.ui.resolve(e)
  const waiting = b.tickets.some(r => r.actions.includes('land'))
  return (
    <Box key="sysop-band">
      <Text dimColor>{bandText(b)} </Text>
      <Button key="open-board" label="board" hotkey="1" plain onPress={() => $.ui.open({ id: BOARD, title: 'sysop board' })} />
      {waiting && <Text> </Text>}
      {waiting && <Button key="land-ready" label="land ready" hotkey="2" plain onPress={() => landReady($)} />}
    </Box>
  )
}

const TITLES = { needs_you: 'Needs you', working: 'Working', ready: 'Ready to land', landed: 'Landed' }

function row($, e, r, open) {
  const { Box, Button, Text } = $.ui.resolve(e)
  const id = `${r.repo}:${r.id}`
  return (
    <Box key={`row:${id}`} flexDirection="column">
      <Box>
        <Text>{r.name} </Text>
        <Text dimColor>({r.status}) </Text>
        {r.actions.includes('land') && (
          <Button key={`land:${id}`} label="Land" variant="primary"
            onPress={() => press($, ['land', `${r.repo}:sysop/${r.id}`, '--land'])} />
        )}
        <Button key={`why:${id}`} label="Why" onPress={() => update($, why, w => (w === id ? null : id))} />
        {r.actions.includes('halt') && (
          <Button key={`halt:${id}`} label="Halt repo" onPress={() => press($, ['--cwd', r.repo, 'halt', '--reason', `halted from the board at ${r.name}`])} />
        )}
      </Box>
      {open === id && <Text dimColor>  {r.last || 'no log yet'}</Text>}
      {open === id && r.report && <Text dimColor>  report: {r.report}</Text>}
    </Box>
  )
}

// --- Wake-ups: Claude sleeps while workers build. ------------------------------------------------------------
// Every 10 s the mod beats its heart (so `sysop.py session` says wakes: true) and, while no turn runs, asks
// `sysop.py wake` whether this session's fleet run (in any registered repo) has more than waiting to do.
// A set of due actions wakes it once, after 20 s of quiet so finishes that land together wake it together.

const WAKE_EVERY = 10000
const COALESCE = 20000
// A plugin's prompt may not start with a slash: it asks for the skill in words instead.
const WAKE_TEXT = why => `sysop: the fleet has work for you (${why}). Resume it: run the sysop:fleet skill with --resume.`
let busy = false // a main turn is running: the coordinator (or the operator) is already at work
let pending = null // { key, since }: due actions seen, waiting out the coalescing window
let woken = '' // the set of due actions the last wake-up was for

async function watch($) {
  const [id, dir] = await Promise.all([$.session.id(), cacheDir($)])
  if (dir) await $.fs.write(`${dir}/mod-${id}.json`, JSON.stringify({ at: (await $.clock.now()) / 1000 }))
  if (busy) {
    pending = null
    return
  }
  const out = await $.process.run(sysopArgv($, ['wake', '--session', id]), { timeoutMs: 60000 })
  let state = { wake: false }
  try {
    state = JSON.parse(out.stdout)
  } catch {}
  const now = await $.clock.now()
  if (!state.wake || state.key === woken) {
    if (!state.wake) pending = null
    return
  }
  if (!pending || pending.key !== state.key) {
    pending = { key: state.key, since: now }
    return
  }
  if (now - pending.since < COALESCE) return
  woken = state.key
  pending = null
  await $.prompt.submit({ text: WAKE_TEXT(state.why) })
}

// --- Issue intake: lanes that set `intake.every_minutes` take their labelled GitHub issues in on a timer. ----
// New tickets are the fleet's next launches, so the wake-up watcher above wakes the coordinator for them.
const lastIntake = {} // lane -> when it last took issues in

async function takeIssuesIn($) {
  const out = await $.process.run(sysopArgv($, ['settings', '--key', 'lanes']), { timeoutMs: 30000 })
  let lanes = {}
  try {
    lanes = (out.exitCode === 0 && JSON.parse(out.stdout)) || {}
  } catch {}
  const now = await $.clock.now()
  for (const [lane, conf] of Object.entries(lanes)) {
    const every = conf?.intake?.every_minutes
    if (!every || (lane in lastIntake && now - lastIntake[lane] < every * 60000)) continue
    lastIntake[lane] = now
    const got = await $.process.run(sysopArgv($, ['lane', 'intake', lane]), { timeoutMs: 120000 })
    try {
      const added = JSON.parse(got.stdout).added ?? []
      if (added.length) $.ui.toast(`sysop: ${added.join(', ')} joined lane ${lane}`)
    } catch {
      $.ui.toast(`sysop: issue intake for lane ${lane} failed: ${say(got).split('\n')[0]}`)
    }
  }
}

// --- Approve in place (interactive only; operator-approved 2026-10-11 and 2026-10-12): a destructive git
// command the guard stopped is put to the operator in a dialog, run once if they press Run once, and logged.
// Only that class is offered: credential reads, forged records and AI attribution never reach the dialog.
// Headless, no dialog can open and the guard's refusal stands.
const OFFERED = ['force push', 'git reset --hard', 'git clean -f', 'forced branch deletion', 'git checkout -- <path>',
  'git switch --discard-changes', 'git restore', 'git stash drop', 'forced worktree removal']

export function offered(reason) {
  const found = /sysop guard blocked: ([^\n]*)/.exec(String(reason || ''))
  return found && OFFERED.some(start => found[1].startsWith(start)) ? found[1].split(' (')[0].split('. ')[0] : null
}

async function approveOnce($, e, next) {
  const verdict = await next(e)
  const why = verdict.decision === 'deny' && verdict.hook === 'PreToolUse' ? offered(verdict.reason) : null
  if (!why) return verdict
  const command = String(e.input?.command ?? '').slice(0, 300)
  let answer
  try {
    answer = await $.ui.ask(`sysop's guard stopped this (${why}): ${command} — run it once?`, ['Run once', 'Refuse'])
  } catch {
    return verdict // nobody to ask (headless), or the dialog was dismissed
  }
  if (answer !== 'Run once') return verdict
  const root = await $.session.root()
  const path = `${root}/.sysop/approvals.jsonl`
  const before = await $.fs.read(path).catch(() => '')
  const row = { at: new Date(await $.clock.now()).toISOString(), session: await $.session.id(), command, reason: why }
  await $.fs.write(path, `${before}${JSON.stringify(row)}\n`)
  return { decision: 'allow', reason: `the operator approved it once in the dialog (${why})` }
}

export const register = on => {
  on('tool.check', { tool: 'Bash' }, approveOnce).catch(($, e, next) => next(e))

  on('turn.start', ($, e, next) => {
    busy = true
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const b = await read($, board)
    if (e.props.hasSurvey || !b) return next(e)
    const { working, needs, ready } = counts(b.tickets)
    if (!working && !needs && !ready) return next(e)
    return band($, e, b)
  })

  on('ui.render', { component: 'Pane', requestId: BOARD }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const b = await read($, board)
    const open = await read($, why)
    if (!b) return <Text>sysop: reading the board…</Text>
    return (
      <Box flexDirection="column">
        <Box>
          <Text dimColor>{bandText(b)} </Text>
          {b.tickets.some(r => r.actions.includes('land')) && (
            <Button key="land-all" label="Land all ready" variant="primary" onPress={() => landReady($)} />
          )}
        </Box>
        {Object.entries(TITLES).map(([column, title]) => {
          const rows = b.tickets.filter(r => r.column === column)
          return rows.length === 0 ? null : (
            <Box key={`col:${column}`} flexDirection="column">
              <Text bold>{title} ({rows.length})</Text>
              {rows.map(r => row($, e, r, open))}
            </Box>
          )
        })}
        {b.tickets.length === 0 && <Text dimColor>No tickets. Next: {b.next}</Text>}
        {b.plan && <Text dimColor>{b.plan}</Text>}
      </Box>
    )
  })

  on('session.measure', async ($, e, next) => {
    if (e.changed?.includes('rateLimits')) await recordUsage($, e.rateLimits)
    return next(e)
  }).catch(($, e, next) => next(e))
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) busy = false
    const result = await next(e)
    if (e.agentId === undefined) await recordUsage($, (await $.session.usage()).rateLimits)
    return result
  }).catch(($, e, next) => next(e))

  for (const tool of ['Agent', 'Task']) {
    // Fails open: with the mod's lookup broken, the guard still asks for the model, as with mods off.
    on('tool.call', { tool }, withAgentModel).catch(($, e, next) => next(e))
  }

  on('session.start', async ($, e, next) => {
    if (e.surface !== null) { // nothing draws under -p or the SDK: no board to keep fresh
      await refresh($)
      $.clock.every(5000, () => poll($))
      $.clock.every(60000, () => refresh($))
      $.clock.every(WAKE_EVERY, () => watch($))
      $.clock.every(60000, () => takeIssuesIn($))
    }
    await $.command.register({
      name: 'sysop',
      description: 'sysop board and controls, no tokens: land, halt, preset, lane, doctor (help lists them)',
      argumentHint: '[land <ticket> | halt | unhalt | preset <name|back> | lane … | doctor | help]',
      immediate: true,
    })
    return next(e)
  })

  on('command.run', { command: 'sysop' }, async ($, e) => {
    const argv = plan(e.args || '')
    if (typeof argv === 'string') return { text: argv }
    if (argv.script) {
      const ran = await $.process.run(['bash', `${$.plugin.root}/scripts/${argv.script}`, ...argv.args], { timeoutMs: 120000 })
      return { text: say(ran) }
    }
    if (argv.pane) {
      await refresh($)
      await $.ui.open({ id: argv.pane, title: 'sysop board' })
      return { text: 'sysop board opened' }
    }
    const result = await $.process.run(['python3', `${$.plugin.root}/scripts/sysop.py`, ...argv], { timeoutMs: 600000 })
    if (argv[0] === 'land' && argv[1] === '--ready') await refresh($)
    return { text: argv[1] === '--ready' ? landed(result.stdout || result.stderr) : say(result) }
  })
}
