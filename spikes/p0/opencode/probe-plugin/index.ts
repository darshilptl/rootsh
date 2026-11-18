// OpenCode plugin-API probe (V2 server plugin).
//
// Records every hook event it receives to $ROOTSH_PROBE_EVENTS (one JSON line
// each) and, depending on $ROOTSH_PROBE_MODE, tries one way of stopping a
// tool call:
//
//   record          observe only
//   deny            permission "evaluate" hook sets effect = "deny"
//   ask             permission "evaluate" hook sets effect = "ask"
//   throw           tool "execute.before" hook throws
//   shell-throw     shell "create.before" hook throws
//   setup-throw     plugin setup throws (a crashing plugin)
//   slow-deny       permission "evaluate" waits 20 s (a user deciding), then denies
import { Plugin } from "@opencode/plugin"
import { appendFileSync } from "node:fs"

const mode = process.env.ROOTSH_PROBE_MODE ?? "record"
const sink = process.env.ROOTSH_PROBE_EVENTS

function safeJson(value: unknown): unknown {
  const seen = new WeakSet<object>()
  return JSON.parse(
    JSON.stringify(value, (_key, item) => {
      if (typeof item === "function") return "[function]"
      if (typeof item === "bigint") return item.toString()
      if (item && typeof item === "object") {
        if (seen.has(item)) return "[circular]"
        seen.add(item)
      }
      return item
    }),
  )
}

function record(kind: string, event: unknown): void {
  if (!sink) return
  appendFileSync(sink, JSON.stringify({ kind, mode, event: safeJson(event) }) + "\n")
}

export default Plugin.define({
  id: "rootsh-probe",
  async setup(ctx) {
    record("plugin.setup", { mode })
    if (mode === "setup-throw") throw new Error("rootsh p0 probe: setup crashed")

    await ctx.session.hook("prompt", (event) => record("session.prompt", event))

    await ctx.permission.hook("evaluate", async (event) => {
      record("permission.evaluate", event)
      if (mode === "slow-deny") {
        await new Promise((resolve) => setTimeout(resolve, 20_000))
        event.effect = "deny"
        event.message = "rootsh p0 probe: slow deny"
        record("permission.evaluate.decided", { after_ms: 20_000 })
      }
      if (mode === "deny" || mode === "ask") {
        event.effect = mode
        event.message = `rootsh p0 probe: ${mode}`
      }
    })

    await ctx.tool.hook("execute.before", (event) => {
      record("tool.execute.before", event)
      if (mode === "throw") throw new Error("rootsh p0 probe: blocked by throw")
    })

    await ctx.tool.hook("execute.after", (event) => record("tool.execute.after", event))

    await ctx.shell.hook("create.before", (event) => {
      // The environment carries live credentials; record variable names only.
      record("shell.create.before", { ...event, env: Object.keys(event.env ?? {}).sort() })
      if (mode === "shell-throw") throw new Error("rootsh p0 probe: shell blocked by throw")
    })
  },
})
