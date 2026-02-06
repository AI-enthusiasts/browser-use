# MCP Server Patterns

## LLM Configuration

### Model Priority
1. Explicit parameter in tool call
2. Config file (`llm_config.get('model')`)
3. Environment variable (`BROWSER_USE_AGENT_MODEL`, `BROWSER_USE_EXTRACTION_MODEL`)
4. Provider-specific defaults (only inside provider blocks)

### Extraction LLM
- Initialized in `__init__` (always available)
- Uses `OPENAI_PROXY_BASE_URL` or `http://localhost:8080/v1`
- Model: `extraction_model` config or `BROWSER_USE_EXTRACTION_MODEL` env

### Agent LLM
- Created in `_retry_with_browser_use_agent`
- Provider detection: config > env > model name prefix
- Providers: bedrock, anthropic, google, openai-compatible

## Partial Init Fix

Problem: `_init_browser_session` could timeout leaving partial state.

Solution: Check ALL 3 components before returning:
```python
if self.browser_session and self.tools and self.file_system:
    return
```

Create each component separately if missing.

## Event Synchronization

**Pattern:** Always await event result for critical operations:
```python
event = self.browser_session.event_bus.dispatch(SomeEvent(...))
await event
await event.event_result(raise_if_any=True, raise_if_none=False)
```

**When to use full sync:** navigate, click, send_keys, close_browser
**Fire-and-forget OK:** scroll, type_text, go_back, switch_tab

## Init Lock

Browser session init protected by `self._init_lock`:
```python
async with self._init_lock:
    if self.browser_session and self.tools and self.file_system:
        return
    # ... init code
```

Always await event result after dispatch:
```python
event = self.browser_session.event_bus.dispatch(SomeEvent(...))
await event
await event.event_result(raise_if_any=True, raise_if_none=False)
```

## Markdown Stripping

Use `strip_markdown_json()` from `browser_use.llm.base`:
```python
from browser_use.llm.base import strip_markdown_json
content = strip_markdown_json(response.content)
```

## Non-ASCII Input

For Cyrillic/CJK/Arabic via CDP:
- Check `_is_non_ascii_char(char)` 
- Don't send `code`/`windowsVirtualKeyCode` for non-ASCII
- Text input via `char` event with `text` parameter
