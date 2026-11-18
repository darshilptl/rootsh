#!/usr/bin/env bash
# P0 checks that need the author: root (sudo) or a human click.
#
#   sudo ./run.sh apparmor           K2: deny-inside-allow and signal mediation
#   sudo ./run.sh bpf-lsm            K4: a BPF-LSM program loads and enforces
#   sudo ./run.sh managed-settings   Claude Code managed hooks fire and survive
#                                    a user-level disableAllHooks
#   ./run.sh notify                  desktop Block/Allow buttons (click one)
#   ./run.sh claude-ask              Claude Code interactive "ask" prompt
#
# Every check cleans up after itself and writes out/<check>.json.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/out"
PROBE="$HERE/../claude_code/hook_probe.py"
mkdir -p "$OUT"

die() { echo "error: $*" >&2; exit 1; }

need_root() {
  [[ $EUID -eq 0 ]] || die "run with sudo"
  [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] || die "run with sudo from your user account"
}

as_user() { runuser -u "$SUDO_USER" -- "$@"; }

user_home() { getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6; }

# write_result <check> <key=value>... : values are recorded as strings.
write_result() {
  local check="$1"; shift
  python3 -I -c '
import json, sys
check, pairs = sys.argv[1], sys.argv[2:]
print(json.dumps({"check": check, **dict(p.split("=", 1) for p in pairs)}, indent=2))
' "$check" "$@" > "$OUT/$check.json"
  [[ -n "${SUDO_USER:-}" ]] && chown "$SUDO_USER:" "$OUT" "$OUT/$check.json"
  cat "$OUT/$check.json"
}

# outcome <command...> : "ok" if it succeeds, "denied" otherwise.
outcome() { if "$@" >/dev/null 2>&1; then echo ok; else echo denied; fi; }

check_apparmor() {
  need_root
  local profile="$HERE/apparmor/rootsh-p0-probe.profile" dir=/tmp/rootsh-p0-apparmor victim
  [[ -e $dir ]] && die "$dir exists; remove it first"
  mkdir -p "$dir/protected" "$dir/allowed"
  echo keep > "$dir/protected/keep.txt"
  chown -R "$SUDO_USER:" "$dir"

  # The victim must be the user's own process: signalling it succeeds
  # unconfined, so a denial under the profile is AppArmor's doing.
  victim="$(as_user sh -c 'sleep 300 >/dev/null 2>&1 & echo $!')"
  # EXIT (not RETURN) so cleanup also runs when set -e aborts mid-check.
  trap "apparmor_parser -R $(printf %q "$profile") 2>/dev/null; kill $victim 2>/dev/null; rm -rf $dir" EXIT
  apparmor_parser -r "$profile"

  confined() { as_user aa-exec -p rootsh-p0-probe -- sh -c "$1"; }
  write_result apparmor \
    "profile_loaded=$(grep -q '^rootsh-p0-probe ' /sys/kernel/security/apparmor/profiles && echo yes || echo no)" \
    "confined_write_allowed=$(outcome confined "touch $dir/allowed/new")" \
    "confined_write_protected=$(outcome confined "touch $dir/protected/new")" \
    "confined_unlink_protected=$(outcome confined "rm $dir/protected/keep.txt")" \
    "confined_signal0_unconfined_process=$(outcome confined "kill -0 $victim")" \
    "confined_sigcont_unconfined_process=$(outcome confined "kill -CONT $victim")" \
    "unconfined_write_protected=$(outcome as_user touch "$dir/protected/control")" \
    "unconfined_sigcont=$(outcome as_user kill -CONT "$victim")" \
    "protected_file_intact=$([[ -f $dir/protected/keep.txt ]] && echo yes || echo no)"
}

check_bpf_lsm() {
  need_root
  local missing=()
  command -v clang >/dev/null || missing+=(clang)
  [[ -f /usr/include/bpf/bpf_helpers.h ]] || missing+=(libbpf-dev)
  if (( ${#missing[@]} )); then
    write_result bpf_lsm status=missing_prerequisites "install=sudo apt install ${missing[*]}"
    return 1
  fi

  local build pin=/sys/fs/bpf/rootsh_p0 dir=/tmp/rootsh-p0-bpf
  [[ -e $pin || -e $dir ]] && die "$pin or $dir exists; remove it first"
  build="$(mktemp -d)"
  trap "rm -rf $pin $(printf %q "$build") $dir" EXIT
  bpftool btf dump file /sys/kernel/btf/vmlinux format c > "$build/vmlinux.h"
  clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -Wno-missing-declarations -I"$build" \
    -c "$HERE/bpf_lsm/deny_unlink.bpf.c" -o "$build/deny_unlink.bpf.o"

  mkdir -p "$dir"
  touch "$dir/rootsh_p0_deny" "$dir/rootsh_p0_deny_longer" "$dir/other_file"
  chown -R "$SUDO_USER:" "$dir"
  bpftool prog loadall "$build/deny_unlink.bpf.o" "$pin" autoattach

  # Measure everything first, then unload, so a failed unload is recorded
  # instead of aborting the check (set -e) before the result is written.
  local user_target root_target prefix other unloaded after
  user_target="$(outcome as_user rm "$dir/rootsh_p0_deny")"
  root_target="$(outcome rm "$dir/rootsh_p0_deny")"
  prefix="$(outcome as_user rm "$dir/rootsh_p0_deny_longer")"
  other="$(outcome as_user rm "$dir/other_file")"
  unloaded="$(outcome rm -rf "$pin")"
  after="$(outcome as_user rm "$dir/rootsh_p0_deny")"
  write_result bpf_lsm status=ran \
    "unlink_target_as_user_while_loaded=$user_target" \
    "unlink_target_as_root_while_loaded=$root_target" \
    "unlink_name_with_target_prefix_while_loaded=$prefix" \
    "unlink_other_while_loaded=$other" \
    "unpin=$unloaded" \
    "unlink_target_after_unload=$after"
}

check_managed_settings() {
  need_root
  local etc=/etc/claude-code file=/etc/claude-code/managed-settings.json created_dir=no
  [[ -e $file ]] && die "$file already exists; this check will not overwrite it"
  local claude ws events
  claude="$(user_home)/.local/bin/claude"
  [[ -x $claude ]] || die "claude not found at $claude"
  ws="$(as_user mktemp -d)"
  events="$ws/managed_events.jsonl"

  [[ -d $etc ]] || { mkdir -p "$etc"; created_dir=yes; }
  trap "rm -f $file; [[ $created_dir == yes ]] && rmdir $etc; rm -rf $(printf %q "$ws")" EXIT
  python3 -I -c '
import json, shlex, sys
probe, events = sys.argv[1], sys.argv[2]
hook = {"type": "command", "command": f"python3 -I {shlex.quote(probe)} record {shlex.quote(events)}"}
print(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}}))
' "$PROBE" "$events" > "$file"
  chmod 0644 "$file"

  local status=0
  (cd "$ws" && as_user env HOME="$(user_home)" "$claude" -p \
    "Use the Bash tool to run exactly: echo rootsh-p0. Then reply DONE." \
    --model haiku --permission-mode bypassPermissions \
    --settings '{"disableAllHooks": true}' --output-format json >/dev/null) || status=$?

  # Other Claude Code sessions also fire the managed hook while the file
  # exists, so look for this check's own command, not just any event.
  write_result managed_settings \
    "path=$file" \
    "mode=$(stat -c %a "$file")" \
    "claude_exit=$status" \
    "managed_hook_fired_despite_user_disableAllHooks=$(grep -q 'echo rootsh-p0' "$events" 2>/dev/null && echo yes || echo no)"
}

check_notify() {
  # notify-send -A is refused on this host (P0 §5), so the probe talks to
  # org.freedesktop.Notifications directly.
  echo "A notification with Block / Allow buttons will appear. Click 'Allow' within 60 s."
  python3 -I "$HERE/../notify/dbus_notify_probe.py" 60 2>/dev/null | tail -1 > "$OUT/notify_raw.json"
  write_result notify "raw=$(cat "$OUT/notify_raw.json")" "expected_action=allow"
  rm -f "$OUT/notify_raw.json"
}

check_claude_ask() {
  local ws answer
  ws="$(mktemp -d)"
  local settings
  settings="$(python3 -I -c '
import json, shlex, sys
probe, events = sys.argv[1], sys.argv[2]
hook = {"type": "command", "command": f"python3 -I {shlex.quote(probe)} ask {shlex.quote(events)}"}
print(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}}))
' "$PROBE" "$ws/events.jsonl")"
  echo "Claude Code will start. When it asks permission to run 'touch ./marker', choose YES, then type /exit."
  (cd "$ws" && claude --model haiku --settings "$settings" "Use the Bash tool to run exactly: touch ./marker")
  read -r -p "Did a permission prompt appear for the touch command? [y/n] " answer
  write_result claude_ask "prompt_appeared=$answer" \
    "marker_created_after_yes=$([[ -f $ws/marker ]] && echo yes || echo no)"
  rm -rf "$ws"
}

case "${1:-}" in
  apparmor) check_apparmor ;;
  bpf-lsm) check_bpf_lsm ;;
  managed-settings) check_managed_settings ;;
  notify) check_notify ;;
  claude-ask) check_claude_ask ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
