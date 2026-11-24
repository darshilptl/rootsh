// OpenCode recording plugin for gold-set collection (design 04 §6).
//
// Appends {hook, at, payload} lines to $ROOTSH_EVENTS. Before each tool call
// it asks rootsh's collection policy ($ROOTSH_PYTHON -m rootsh.research.collect.net_guard):
// exit 0 lets the call run inside the sandbox, anything else blocks it by
// throwing (P0 §2). Setup never throws: OpenCode fails open on setup errors.
import { Plugin } from "@opencode/plugin"
import { spawnSync } from "node:child_process"
import { appendFileSync } from "node:fs"

const sink = process.env.ROOTSH_EVENTS
const python = process.env.ROOTSH_PYTHON

function record(hook: string, payload: unknown): void {
  if (!sink) return
  try {
    appendFileSync(sink, JSON.stringify({ hook, at: new Date().toISOString(), payload }) + "\n")
  } catch (error) {
    // Never throw from setup (fails open); a failed write is logged for the harness.
    try {
      appendFileSync(sink.replace(/[^/]*$/, "hook-errors.log"), `${new Date().toISOString()} ${error}\n`)
    } catch {}
  }
}

function denyReason(event: unknown): string | null {
  if (!python) return "rootsh collection: policy unavailable"
  const result = spawnSync(python, ["-m", "rootsh.research.collect.net_guard"], { input: JSON.stringify(event) })
  if (result.status === 0) return null
  const out = (result.stdout?.toString() ?? "").trim()
  return result.status === 3 && out ? out : `rootsh collection: policy failed (exit ${result.status})`
}

export default Plugin.define({
  id: "rootsh-collect",
  async setup(ctx) {
    // Proof the recorder loaded: OpenCode runs the agent anyway when a plugin
    // fails to load (P0 §2), and the harness must not mistake that for a run
    // with no tool calls.
    record("rootsh.loaded", { plugin: "rootsh-collect" })
    await ctx.session.hook("prompt", (event) => record("session.prompt", event))
    await ctx.tool.hook("execute.before", (event) => {
      record("tool.execute.before", event)
      const reason = denyReason(event)
      if (reason !== null) {
        record("rootsh.deny", { reason, id: (event as { id?: string }).id ?? null })
        throw new Error(reason)
      }
    })
    await ctx.tool.hook("execute.after", (event) => record("tool.execute.after", event))
  },
})
