# Git Bash 8186 char `-c` Limit: Alternatives Research

**Date:** 2026-02-09
**Context:** OpenCode CLI spawns `bash -c "command"` via Node.js `child_process.spawn()`. Git Bash (MSYS2) silently truncates at 8186 chars.

## Root Cause (VERIFIED)

Limit is in `msys-2.0.dll` (MSYS2 runtime), NOT in `bash.exe`. The runtime converts POSIX argv to Windows command-line string with conservative ~8K limit. `CreateProcess` Win32 API supports 32,767 chars. Any shell calling `CreateProcess` directly bypasses the limit.

Evidence:
- `getconf ARG_MAX` reports 32000 (Cygwin-to-Cygwin limit), but actual truncation at 8186
- perl.exe (same msys-2.0.dll) receives 32K args OK — because perl is native Win32
- Replacing bash.exe WON'T help — the DLL is the bottleneck
- Confirmed by Cygwin maintainer mailing list (Eric Blake, Corinna Vinschen, 2005)

## Solutions Ranked

### ⭐ #1: stdin pipe (BEST — zero dependencies, zero risk)

```typescript
// Instead of: spawn(command, { shell: bashPath })  // hits 8186 limit
// Use:
const proc = spawn(bashPath, [], { stdio: ["pipe", "pipe", "pipe"], cwd, env })
proc.stdin.end(command + "\n")
```

- **Limit:** None (pipe buffer, effectively unlimited)
- **Bash compat:** 100% — same bash, different input method
- **Risk:** Zero — stdin was already "ignore" in OpenCode
- **Complexity:** ~10 lines changed in bash.ts
- **Upstream PR:** Modify `packages/opencode/src/tool/bash.ts`

### ⭐ #2: busybox-w32 ash (BEST lightweight alternative shell)

- **Binary:** ~700KB single .exe, native Win32, NO msys-2.0.dll
- **`-c` limit:** ~32,767 chars (CreateProcess limit)
- **Bash compat:** ~60-70% (ash + some bash features)
- **Missing:** arrays, `${var//pat/rep}`, process substitution `<(...)`, `shopt`, `BASH_REMATCH`
- **Install:** Single file download from frippery.org
- **Bundleable:** Yes — trivially with npm package
- **Proof:** w64devkit (4K GitHub stars) uses it as sole shell
- **Repo:** github.com/rmyorston/busybox-w32 (812 stars, active)

### #3: WSL (viable fallback, NOT drop-in)

- **`-c` limit:** ~131,072 chars per argument (Linux MAX_ARG_STRLEN)
- **Bash compat:** 100% (real Linux bash)
- **Gotchas:** Path translation (C:\ → /mnt/c/), cold start 2-5s, /mnt/c/ 3-6x slower (WSL2), env var issues
- **Availability:** ~50-70% of Windows devs (requires opt-in install)
- **Detection:** `wsl echo ok` (cache result, expensive check)
- **WSL1 better** for this use case (faster /mnt/c/ access)

### #4: PowerShell (higher ceiling, NOT a fix)

- **`-c` limit:** ~32,716 chars (CreateProcess limit)
- **Bash compat:** 0% — completely different syntax
- **Startup:** 1-6s without -NoProfile, ~500ms with
- **Verdict:** ❌ NOT viable — would require bash→PowerShell translation layer

### ❌ NOT viable:
- **MSYS2 standalone bash** — same runtime, same limit
- **Replace Git's bash.exe** — limit is in DLL, not exe
- **cmd.exe** — has its OWN 8192 limit + different syntax
- **dash/mksh/zsh native** — no maintained native Windows builds
- **Cygwin bash** — works but heavy (~3MB cygwin1.dll), path conflicts with MSYS2

## Recommended Implementation for OpenCode

```typescript
// In packages/opencode/src/tool/bash.ts
const GIT_BASH_LIMIT = 8000 // conservative, actual limit 8186

if (process.platform === "win32" && shell.includes("bash") && params.command.length > GIT_BASH_LIMIT) {
    proc = spawn(shell, [], {
        cwd, env: { ...process.env, ...shellEnv.env },
        stdio: ["pipe", "pipe", "pipe"],
        detached: false,
    })
    proc.stdin!.end(params.command + "\n")
} else {
    // Original path
    proc = spawn(params.command, {
        shell, cwd, env: { ...process.env, ...shellEnv.env },
        stdio: ["ignore", "pipe", "pipe"],
        detached: process.platform !== "win32",
    })
}
```

## Edge Cases (stdin approach)

- **Interactive commands (vim, less):** Already broken (stdin was "ignore") — no regression
- **Signal handling:** Identical — killTree uses taskkill /f /t on Windows
- **Exit codes:** Identical — bash propagates last command's exit code
- **Very large commands (100K+):** Handle backpressure with drain event
- **TTY detection:** `isTTY` is false — same as current behavior
- **`$0` value:** Different (`bash` vs script name) — rarely matters for -c commands
