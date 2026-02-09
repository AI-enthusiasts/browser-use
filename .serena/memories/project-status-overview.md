# Browser-Use Fork — Project Status Overview

**Last updated:** 2026-02-09

## Fork Purpose
Local fork of browser-use with custom fixes for Windows environment and MCP server improvements.

## Version
- browser-use: 0.11.9
- Branch: main

## Completed Work

### 1. MCP Server Fixes (commits 04f0431a, 55c13a40)
- LLM initialization via openai-proxy (localhost:8080/v1/anthropic)
- Partial init fix (check all 3 components before marking initialized)
- Race condition fix (asyncio.Lock on _init_browser_session)
- Atomic file writes in PatternStore.save()
- Configurable model via config/env

### 2. Interactive Element Markers (commit 856092fc)
- HTMLSerializer: `_get_interactive_marker(node)` — tag-specific markers
- Marker format: `[btn:123]`, `[link:456]`, `[input:789 type=text]`, `[select:N]`, `[textarea:N]`
- extract_clean_markdown: `include_interactive=True` parameter
- Uses backend_node_id — same indices as browser_click
- **Tested:** 7/7 tests pass (tests/ci/test_interactive_markers.py)

### 3. Popup/Modal Detection (popup-detection-implementation memory)
- HTMLSerializer.detect_popups() — detects dialog, role=dialog, aria-modal
- extract_clean_markdown integrates popup detection
- Output: "--- POPUP/MODAL DETECTED ---" section before page content

### 4. Windows Fixes
- **Emoji removal** (commit 82f5c337): All emoji removed from log messages — prevents UnicodeEncodeError on cp1251
- **.gitignore** (commit b5482d2a): Windows reserved names (nul, con, aux, etc.)
- **.gitattributes** (commit 71afc407): eol=lf rules

### 5. File Reversion Root Cause (documented, upstream fix spec written)
- Root cause: `nul` file → `git add .` fatal → stale snapshot → `/undo` overwrites
- Fix spec: `docs/opencode-snapshot-fix-spec.md` (743 lines)
- Issue: anomalyco/opencode#12719

## Known Issues (Not Fixed)

### Navigation Verification (navigation-verification-issue memory)
- `_navigate_and_wait()` returns normally on timeout — no exception raised
- MCP returns "Navigated to: URL" even when page didn't load
- Fix needed: raise TimeoutError on timeout, verify actual URL

### Pattern Learning Remaining Issues
- `__getattr__` missing return type hint
- `induce_workflows()` LLM response not validated

## Configuration
- LLM proxy: `http://localhost:8080/v1/anthropic`
- Model: `claude-haiku-4-5` (configurable via config.json)
- API key: `not-needed` (proxy handles auth)
- Config file: `~/.config/browseruse/config.json`

## Test Infrastructure
- Framework: pytest + asyncio
- Fixtures: `tests/ci/conftest.py` (HTTPServer, browser_session, etc.)
- Key tests:
  - `tests/ci/test_interactive_markers.py` — 7 tests, markers validation
  - `tests/ci/test_popup_detection.py` — 16 tests
  - `tests/ci/test_json_filtering.py` — 8 tests
  - `tests/ci/test_pattern_learning.py` — 60+ tests
  - `tests/ci/browser/test_non_ascii_input.py` — 30+ tests

## Memory Index (15 remaining)
- `project-status-overview` — THIS FILE
- `windows-environment-issues` — Windows-specific bugs and fixes
- `mcp-llm-extraction-config` — LLM proxy configuration details
- `mcp-server-patterns` — MCP server implementation patterns
- `re-implementation-interactive-markers-exact-code` — Marker implementation reference
- `selector-map-api-minimal-usage` — DOMSelectorMap API reference
- `interactive-elements-extraction-architecture` — Architecture of interactive elements
- `file-reversion-root-cause-proven` — Root cause of file reversion bug
- `asyncio_lock_patterns` — asyncio.Lock patterns in project
- `atomic_file_write_patterns` — Atomic write patterns in project
- `modal-popup-extraction-analysis` — Popup/modal visibility analysis
- `popup-detection-implementation` — Popup detection implementation details
- `navigation-verification-issue` — Known bug: silent navigation failure
- `test_quality_analysis` — Test quality review
- `pattern_learning_review` — PatternLearning code review
