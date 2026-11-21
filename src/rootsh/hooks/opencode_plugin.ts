// rootsh defense plugin for OpenCode (docs/design/08-hooks-install.md §3).
//
// Installed by `rootsh install` with CONFIG filled in; never edit by hand.
// Every hook spawns the Python bridge (rootsh.hooks.opencode), which talks to
// the daemon through rootsh.daemon.client.decide(). A tool call runs only if
// the bridge exits 0 AND prints {"decision":"allow"}; anything else throws,
// which blocks the call (P0 §2). Setup never throws: OpenCode fails open on
// setup errors.
import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import { appendFileSync } from "node:fs"

const CONFIG = __ROOTSH_CONFIG__ as { python: string; socket: string; errors: string }

// The bridge's own timeouts: 5 s for a decision, ~70 s for a desktop ask.
const DECIDE_TIMEOUT_MS = 90_000
const RECORD_TIMEOUT_MS = 15_000
const MAX_OUTPUT_BYTES = 64 * 1024

type Bridge = { code: number | null; line: string }

function logError(text: string): void {
  try {
    appendFileSync(CONFIG.errors, `${new Date().toISOString()} ${text}\n`)
  } catch {}
}

function bridge(hook: string, event: unknown, timeoutMs: number): Promise<Bridge> {
  return new Promise((resolve) => {
    let out = ""
    let settled = false
    const finish = (result: Bridge) => {
      if (settled) return
      settled = true
      resolve(result)
    }
    let child
    try {
      child = spawn(
        CONFIG.python,
        ["-I", "-m", "rootsh.hooks.opencode", "--socket", CONFIG.socket, "--hook", hook],
        { stdio: ["pipe", "pipe", "ignore"], timeout: timeoutMs, killSignal: "SIGKILL" },
      )
    } catch (error) {
      finish({ code: null, line: `spawn failed: ${error}` })
      return
    }
    child.on("error", (error) => finish({ code: null, line: `bridge error: ${error}` }))
    child.stdout.on("data", (chunk: Buffer) => {
      out += chunk.toString()
      if (out.length > MAX_OUTPUT_BYTES) child.kill("SIGKILL")
    })
    child.on("close", (code) => finish({ code, line: out.trim() }))
    child.stdin.on("error", () => {}) // a bridge that exits early is judged by its exit code
    let input: string
    try {
      input = JSON.stringify(event)
    } catch (error) {
      child.kill("SIGKILL")
      finish({ code: null, line: `event not serializable: ${error}` })
      return
    }
    child.stdin.end(input)
  })
}

function parse(line: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(line)
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

async function record(hook: string, event: unknown): Promise<void> {
  const result = await bridge(hook, event, RECORD_TIMEOUT_MS)
  const reply = parse(result.line)
  if (result.code !== 0 || reply?.ok !== true) {
    logError(`${hook} not recorded (exit ${result.code}): ${result.line.slice(0, 500)}`)
  }
}

async function decide(event: unknown): Promise<void> {
  const result = await bridge("tool.execute.before", event, DECIDE_TIMEOUT_MS)
  const reply = parse(result.line)
  if (result.code === 0 && reply !== null && reply.decision === "allow") return
  const reason =
    typeof reply?.reason === "string"
      ? reply.reason
      : `rootsh failed closed (exit ${result.code}): ${result.line.slice(0, 500)}`
  throw new Error(reason)
}

export default Plugin.define({
  id: "rootsh",
  async setup(ctx) {
    try {
      await ctx.session.hook("prompt", (event) => record("session.prompt", event))
      await ctx.tool.hook("execute.before", (event) => decide(event))
      await ctx.tool.hook("execute.after", (event) => record("tool.execute.after", event))
    } catch (error) {
      // Registration failed: OpenCode would run every tool unchecked. Nothing
      // in setup can stop that (it fails open), so leave a loud trace for
      // `rootsh doctor` and the author.
      logError(`setup failed, rootsh is NOT active: ${error}`)
    }
  },
})
