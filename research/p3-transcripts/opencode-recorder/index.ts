// OpenCode plugin for recording P3 transcripts.
//
// Appends session.prompt / tool.execute.before / tool.execute.after events to
// $ROOTSH_P3_EVENTS. Read-only tools and read-only shell commands run (rootsh's
// effect extractor decides, via $ROOTSH_P3_PYTHON readonly.py); everything else
// is blocked, so destructive proposals are recorded but never executed.
import { Plugin } from "@opencode/plugin"
import { spawnSync } from "node:child_process"
import { appendFileSync } from "node:fs"

const sink = process.env.ROOTSH_P3_EVENTS
const python = process.env.ROOTSH_P3_PYTHON
const checker = process.env.ROOTSH_P3_READONLY
const READ_ONLY = new Set(["read", "glob", "grep", "list"])

function readOnlyCommand(command: unknown): boolean {
  if (typeof command !== "string" || !python || !checker) return false
  const cwd = process.env.PWD ?? "/"
  return spawnSync(python, [checker, cwd], { input: command }).status === 0
}

function record(hook: string, payload: unknown): void {
  if (sink) appendFileSync(sink, JSON.stringify({ hook, payload }) + "\n")
}

export default Plugin.define({
  id: "rootsh-p3-recorder",
  async setup(ctx) {
    await ctx.session.hook("prompt", (event) => record("session.prompt", event))
    await ctx.tool.hook("execute.before", (event) => {
      record("tool.execute.before", event)
      const allowed =
        READ_ONLY.has(event.tool) || (event.tool === "shell" && readOnlyCommand(event.input?.command))
      if (!allowed) throw new Error("rootsh P3 recording: only read-only actions may run")
    })
    await ctx.tool.hook("execute.after", (event) => record("tool.execute.after", event))
  },
})
