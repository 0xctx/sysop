// The sysop mod's state contract: what its band and pane draw from (hooks/sysop.mod.jsx).

/** One ticket on the board, as `sysop.py status --all --json` lists it. */
export type SysopTicket = {
  id: string
  name: string
  repo: string
  lane: string | null
  status: string
  column: 'needs_you' | 'working' | 'ready' | 'landed'
  last: string
  report: string | null
  actions: ('land' | 'why' | 'halt')[]
}

/** The board: `status --all --json`, the fields the mod reads. */
export type SysopBoard = {
  tickets: SysopTicket[]
  repos: string[]
  halted: string[]
  plan: string
  plan_percent: number | null
  spend_usd: number | null
  session_model: string | null
  next: string
}

declare module 'claude-code' {
  interface PluginState {
    sysop: { board: SysopBoard | null; why: string | null }
  }
}
