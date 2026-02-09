# Windows Environment Issues — Browser-Use Fork

**Last updated:** 2026-02-09

## FIXED Issues

### 1. cp1251 Encoding Crash (FIXED — commit 82f5c337)
**Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode character` on every log message with emoji
**Root cause:** Windows console uses cp1251, Python logging tries to encode emoji
**Fix:** Removed ALL emoji from log messages (654 emoji, 53 files)
**Verification:** 7/7 tests pass, zero "--- Logging error ---" in output

### 2. `nul` File Creation (FIXED — commit b5482d2a)
**Symptom:** `2>nul` in bash creates literal file named `nul` on Windows
**Root cause:** Git Bash on Windows treats `nul` as regular filename, not /dev/null
**Fix:** Added Windows reserved names to .gitignore (nul, con, aux, prn, com1-9, lpt1-9)
**Related:** bash-syntax-fixer hook in damn-opencode converts `>nul` → `>/dev/null`

### 3. CRLF Line Endings (FIXED — commit 71afc407)
**Symptom:** Files appear modified after checkout due to autocrlf
**Fix:** .gitattributes with `* text=auto eol=lf` rules

### 4. Heredoc Body Corruption (FIXED — damn-opencode commit f0bfee3)
**Symptom:** bash-syntax-fixer hook modifies content inside heredoc body:
  - `>nul` → `>/dev/null` inside heredoc
  - `C:\Users\...` → `C:/Users/...` inside heredoc
**Root cause:** fixNulRedirection and fixBackslashPaths applied to entire command including heredoc body
**Fix:** splitHeredocSegments() — splits command into heredoc vs non-heredoc segments, applies fixes only to non-heredoc parts

## KNOWN Issues (Not Fixed)

### 5. python3 Microsoft Store Stub
**Symptom:** `python3` opens Microsoft Store instead of running Python
**Workaround:** Use `uv run python` or `python` (not `python3`)

### 6. Heredoc Truncation at ~128 Lines
**Symptom:** Large heredocs (>128 lines) may be truncated in Git Bash (MINGW64)
**Status:** Not reproduced through bash-syntax-fixer hook (test 3: 200 lines pass)
**Hypothesis:** Git Bash terminal limitation, not hook issue
**Workaround:** Use `serena:create_text_file` or `write` tool instead of heredoc for large files

### 7. Console Encoding for Non-ASCII
**Workaround:** Always use `-X utf8` flag or `sys.stdout.reconfigure(encoding='utf-8')` when running Python scripts that output non-ASCII

## Environment Details
- OS: Windows (cp1251 console encoding)
- Shell: Git Bash (MINGW64)
- Python: via uv (NOT python3)
- Browser: Chromium via CDP
