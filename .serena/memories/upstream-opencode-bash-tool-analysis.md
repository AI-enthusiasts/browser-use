# Upstream OpenCode Bash Tool Analysis

## Source Location
- Repo: `~/repos/opencode-upstream/` (NOT `~/repos/opencode/` — that doesn't exist)
- Bash tool: `packages/opencode/src/tool/bash.ts`
- Shell layer: `packages/opencode/src/shell/shell.ts`
- Truncation: `packages/opencode/src/tool/truncation.ts`
- Tool base: `packages/opencode/src/tool/tool.ts`
- LLM session: `packages/opencode/src/session/llm.ts`
- Provider transform: `packages/opencode/src/provider/transform.ts`
- OpenAI chat model: `packages/opencode/src/provider/sdk/copilot/chat/openai-compatible-chat-language-model.ts`

## Key Findings

### 1. Command Execution
```typescript
spawn(params.command, {
  shell: Shell.acceptable(),  // Git Bash on Windows
  cwd,
  stdio: ["ignore", "pipe", "pipe"],
})
```
- Uses `spawn` (NOT `exec`) — no maxBuffer limit
- Command passed as string to shell
- **No input command size limit** — `z.string()` without `.max()`
- stdin is "ignore" — command goes via shell argument

### 2. Shell Selection (Windows)
Priority: `OPENCODE_GIT_BASH_PATH` → Git's `bin/bash.exe` → `COMSPEC`/`cmd.exe`
- On typical Windows with Git: uses Git Bash
- `2>nul` in Git Bash → creates literal file `nul` (not NUL device)
- OpenCode does NOT convert `2>nul` → responsibility of LLM or plugin

### 3. Output Truncation (NOT Input)
```
MAX_LINES = 2000
MAX_BYTES = 50KB (50 * 1024)
```
Applied ONLY to tool OUTPUT via `Tool.define` wrapper.
NOT applied to input command parameter.

### 4. Metadata Truncation
```
MAX_METADATA_LENGTH = 30_000
```
Only for UI display metadata, not for command execution.

### 5. LLM Output Token Limit
```
OUTPUT_TOKEN_MAX = 32_000 tokens (~128K chars)
```
This is max output tokens for entire LLM response (thinking + text + tool calls).
NOT a per-parameter limit.

### 6. Streaming Tool Call Assembly
```typescript
toolCall.function!.arguments += toolCallDelta.function?.arguments ?? ""
```
Simple string concatenation — no truncation, no size limit.
`isParsableJson()` checks if accumulated JSON is valid — only triggers on complete JSON.

### 7. nul ↔ Bash Tool Connection
Chain: LLM generates `2>nul` → bash tool spawns in Git Bash → Git Bash creates file `nul` → git add fatal → stale snapshot → /undo overwrites

## VERIFIED: Heredoc Truncation Root Cause

**VERIFIED CAUSE: Git Bash (MSYS2) silently truncates `-c` argument to exactly 8186 chars on Windows (2^13) on Windows.**

Reproduction (Node.js spawn, isolated — NOT inside OpenCode):
- `spawn(gitBash, ["-c", echo_8186])` → bash received 8186 chars (full)
- `spawn(gitBash, ["-c", echo_8187])` → bash received 8186 chars (1 char silently dropped)
- `spawn(gitBash, ["-c", echo_32000])` → bash received 8186 chars (23814 chars silently dropped)
- `spawn("cmd.exe", ["/c", echo_30000])` → NO truncation (cmd.exe has no such limit)
- `spawn("powershell.exe", ["-Command", ps_9000])` → NO truncation
- `spawn(gitBash, [], { stdin: cmd_20000 })` → NO truncation via stdin

**Module: Git Bash (GNU bash 5.2.26, x86_64-pc-msys) — `-c` argument processing.**

Isolation proof (each step verified):
1. Node.js spawn delivers full argv via CreateProcess — PowerShell receives 32000 chars OK
2. MSYS2 perl.exe (same msys-2.0.dll runtime as bash) receives 32000 chars via argv OK
3. MSYS2 gawk.exe (same runtime) receives 32000 chars via argv OK
4. bash.exe via stdin (no -c) handles 16000 chars OK
5. ONLY bash.exe -c truncates — at exactly 8186 chars

Conclusion: CreateProcess + MSYS2 runtime deliver full argument to bash.exe.
Bash receives it, but its internal `-c` argument processing truncates to 8186 chars.
This is a bug/limit in GNU bash 5.2.26 (x86_64-pc-msys) `-c` handler.

Bash silently drops everything beyond 8186 chars in the `-c` argument.
Heredoc without closing delimiter → bash reads to EOF → `warning: here-document delimited by end-of-file`.

**Location in upstream:** `packages/opencode/src/tool/bash.ts` line 167:
```typescript
const proc = spawn(params.command, {
  shell,  // ← Git Bash on Windows
  ...
  stdio: ["ignore", "pipe", "pipe"],  // ← stdin is "ignore", command goes via -c
})
```

**Fix:** Change stdin from "ignore" to "pipe", write command via stdin instead of `-c`.
Verified: stdin method handles 20KB+ without truncation.

**Our bash-syntax-fixer (a20514e) is NOT related to this truncation.**
The limit existed before our plugin. Our plugin's bug was different: it modified heredoc body content
(paths, nul redirections) which was fixed in f0bfee3.

## OpenCode Version
- Installed: 1.1.53
- Binary: `~/AppData/Roaming/npm/node_modules/opencode-ai/bin/opencode`
