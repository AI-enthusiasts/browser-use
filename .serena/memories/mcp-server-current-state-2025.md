# MCP Server Current State (2025) - UPSTREAM REWRITE

## CRITICAL CHANGES FROM PREVIOUS STATE

### 1. Lock Pattern STILL PRESENT
- **Location:** `__init__` line ~220, `_init_browser_session` line 582
- **Status:** `self._init_lock = asyncio.Lock()` is STILL THERE
- **Memory note:** Previous memory said "upstream removed the lock" — THIS IS WRONG
- **Usage:** `async with self._init_lock:` guards initialization (line 587)

### 2. PatternLearningAgent STILL IMPORTED AND USED
- **Location:** Line 87 imports `PatternLearningAgent`
- **Status:** STILL ACTIVE, NOT REMOVED
- **Memory note:** Previous memory said "upstream removed PatternLearningAgent" — THIS IS WRONG
- **Usage:** Line 723 creates agent: `agent = PatternLearningAgent(...)`

---

## METHOD LOCATIONS & SIGNATURES

### 1. `_get_browser_state(include_screenshot: bool = False) -> str`
- **Lines:** 911-941
- **Tab serialization:** Line 923 — `'tabs': [{'url': tab.url, 'title': tab.title} for tab in state.tabs]`
- **CRITICAL:** Tab objects DO NOT include `tab_id` in the list
- **Tab ID extraction:** Happens LATER in `_list_tabs()` (line 1055) via `tab.target_id[-4:]`
- **Interactive elements:** Lines 926-933 iterate `state.dom_state.selector_map.items()` with index as key
- **Screenshot:** Line 939 includes screenshot if requested

### 2. `_scroll(direction: str = 'down') -> str`
- **Lines:** 1017-1032
- **Parameters:** `direction` (str, default 'down')
- **Return value:** `f'Scrolled {direction}'` (line 1031)
- **Event dispatch:** `ScrollEvent(direction=direction, amount=500)` (lines 1025-1028)
- **Fire-and-forget:** `await event` only, NO `event_result()` call
- **Amount:** Hardcoded to 500 pixels

### 3. `_click(index: int, new_tab: bool = False) -> str`
- **Lines:** 811-863
- **Return values:**
  - Normal click: `f'Clicked element {index}'` (line 862)
  - New tab with href: `f'Clicked element {index} and opened in new tab {full_url[:20]}...'` (line 849)
  - New tab without href: `f'Clicked element {index} (new tab not supported for non-link elements)'` (line 857)
  - Element not found: `f'Element with index {index} not found'` (line 823)
- **Event handling:** Uses `ClickElementEvent` (line 851, 855, 860)
- **Event result:** AWAITS `event.event_result(raise_if_any=True, raise_if_none=False)` (lines 849, 856, 861)
- **New tab logic:** Lines 825-849 extract href, convert relative URLs to absolute, dispatch NavigateToUrlEvent

### 4. `_type_text(index: int, text: str) -> str`
- **Lines:** 865-909
- **Return values:**
  - Sensitive email: `f'Typed <email> into element {index}'` (line 905)
  - Sensitive credential: `f'Typed <credential> into element {index}'` (line 906)
  - Sensitive generic: `f'Typed <sensitive> into element {index}'` (line 908)
  - Normal text: `f"Typed '{text}' into element {index}"` (line 909)
- **Sensitivity detection:** Lines 877-887
  - Email: `'@' in text and '.' in text.split('@')[-1]`
  - Credential: `len >= 16 and has digits and has letters and has special chars`
- **Event:** `TypeTextEvent(node=element, text=text, is_sensitive=..., sensitive_key_name=...)` (lines 900-902)
- **Fire-and-forget:** `await event` only, NO `event_result()` call

### 5. `_extract_content(query, extract_links=False, skip_json_filtering=False, start_from_char=0, output_schema=None) -> str`
- **Lines:** 943-1015
- **Signature:** Simplified from previous — now takes individual parameters, not a dict
- **Implementation:** Lines 973-1000
  - Creates dynamic `ExtractAction` model (lines 973-978)
  - Builds `extract_params` dict (lines 981-987)
  - Calls `self.tools.act()` (lines 990-996)
- **Lazy loading hint:** Lines 1003-1013
  - Checks `state.page_info.pixels_below > 500`
  - Appends note about scrolling to load more
  - Wrapped in try/except — never fails extraction

### 6. `_init_browser_session(allowed_domains=None, **kwargs)`
- **Lines:** 582-638
- **Lock:** `async with self._init_lock:` (line 587)
- **Check:** `if self.browser_session and self.tools and self.file_system: return` (line 588)
- **Profile creation:** Lines 600-618
  - Defaults: downloads_path, wait_between_actions, keep_alive, user_data_dir, device_scale_factor, disable_security, headless
  - Config overrides defaults
  - Tool parameters override config
  - kwargs override everything
- **Components initialized:**
  1. `BrowserSession` (lines 621-625)
  2. `Tools()` (line 628)
  3. `FileSystem` (lines 633-635)
- **LLM:** Already initialized in `__init__` (line 635 comment)

### 7. `_retry_with_browser_use_agent(task, max_steps=100, model=None, allowed_domains=None, use_vision=True) -> str`
- **Lines:** 640-772
- **LLM provider detection:** Lines 651-720
  - Priority: config > env > auto-detect from model name
  - Providers: bedrock, anthropic/claude, google/vertex, openai-compatible
  - Each provider has its own initialization logic
- **Agent creation:** Lines 723-729
  - Uses `PatternLearningAgent` (NOT removed)
  - Passes `patterns_path` from config or env
  - Sets `auto_learn=True`
  - Passes `page_extraction_llm=self.llm`
- **Result formatting:** Lines 732-760
  - Steps count, success status, patterns file path
  - Final result, errors, URLs visited
- **Cleanup:** Lines 762-770
  - Prevents agent from closing shared browser session
  - Sets `agent._agent.browser_session = None`
  - Calls `await agent.close()`

### 8. `_setup_handlers()` - Tool Definitions
- **Lines:** 233-505
- **ALL REGISTERED TOOLS:**

| Tool Name | Lines | Description |
|-----------|-------|-------------|
| `browser_navigate` | 244-254 | Navigate to URL |
| `browser_click` | 255-270 | Click element by index |
| `browser_type` | 271-283 | Type text into input |
| `browser_get_state` | 284-296 | Get page state |
| `browser_extract_content` | 297-325 | Extract content with query |
| `browser_scroll` | 326-338 | Scroll page |
| `browser_go_back` | 339-341 | Go back in history |
| `browser_send_keys` | 342-356 | Send keyboard keys |
| `browser_evaluate` | 357-371 | Execute JavaScript |
| `browser_list_tabs` | 372-373 | List open tabs |
| `browser_switch_tab` | 374-382 | Switch to tab |
| `browser_close_tab` | 383-391 | Close tab |
| `retry_with_browser_use_agent` | 392-421 | Run autonomous agent |
| `browser_list_sessions` | 422-425 | List sessions |
| `browser_close_session` | 426-435 | Close session |
| `browser_close_all` | 436-439 | Close all sessions |

---

## TOOL EXISTENCE VERIFICATION

### ✅ `browser_send_keys` — EXISTS
- **Lines:** 342-356
- **Parameters:** `keys` (string, required)
- **Description:** "Send keyboard keys or shortcuts"
- **Implementation:** `_send_keys()` at lines 1034-1050

### ✅ `browser_evaluate` — EXISTS
- **Lines:** 357-371
- **Parameters:** `expression` (string, required)
- **Description:** "Execute JavaScript code on the current page"
- **Implementation:** `_evaluate_js()` at lines 1052-1088

### ❌ `browser_find_text` — DOES NOT EXIST
- Not in tool list
- No corresponding method in class
- **Alternative:** Use `browser_evaluate` with DOM query or `browser_extract_content` with text query

---

## NOTABLE CHANGES FROM PREVIOUS STATE

### 1. Tab ID Handling
- **Previous memory:** "tab_id still missing"
- **Current state:** Tab ID IS extracted, but ONLY in `_list_tabs()` via `tab.target_id[-4:]`
- **In `_get_browser_state()`:** Tabs are serialized WITHOUT tab_id (only url and title)
- **Implication:** Clients must call `browser_list_tabs` to get tab IDs for switching/closing

### 2. Event Result Awaiting
- **Scroll:** Fire-and-forget (no event_result)
- **Type text:** Fire-and-forget (no event_result)
- **Click:** AWAITS event_result (line 849, 856, 861)
- **Navigate:** AWAITS event_result (line 796)
- **Send keys:** AWAITS event_result (line 1047)
- **Go back:** Fire-and-forget (line 1032)

### 3. Extract Content Signature
- **Previous:** Likely took a dict parameter
- **Current:** Takes individual parameters (query, extract_links, skip_json_filtering, start_from_char, output_schema)
- **Implementation:** Builds dict internally for tools.act()

### 4. Lazy Loading Hint
- **New feature:** Lines 1003-1013
- **Condition:** `state.page_info.pixels_below > 500`
- **Output:** Appends note to extraction result
- **Safety:** Wrapped in try/except

---

## CRITICAL IMPLEMENTATION DETAILS

### Browser State Summary Structure
```python
state = await self.browser_session.get_browser_state_summary()
# state.url, state.title, state.tabs, state.dom_state.selector_map
# state.screenshot (if requested)
# state.page_info.pixels_below (for lazy loading detection)
```

### Element Access Pattern
```python
element = await self.browser_session.get_dom_element_by_index(index)
# element.attributes (dict)
# element.tag_name
# element.get_all_children_text(max_depth=2)
```

### Event Dispatch Pattern
```python
event = self.browser_session.event_bus.dispatch(SomeEvent(...))
await event  # Wait for event to be processed
await event.event_result(raise_if_any=True, raise_if_none=False)  # Wait for result
```

### Session Tracking
- `self.active_sessions[session_id]` stores: session, created_at, last_activity, url
- `_track_session()` called after browser_session.start()
- `_update_session_activity()` called on user actions

---

## MEMORY CORRECTIONS

### WRONG: "upstream removed the lock"
- **Fact:** Lock is STILL PRESENT at line 220 and used at line 587

### WRONG: "upstream removed PatternLearningAgent"
- **Fact:** PatternLearningAgent is STILL IMPORTED (line 87) and USED (line 723)

### CORRECT: "Partial init fix with 3-component check"
- **Fact:** Line 588 checks all 3: `if self.browser_session and self.tools and self.file_system: return`

### CORRECT: "Event synchronization pattern"
- **Fact:** Critical operations (click, navigate, send_keys) await event_result
- **Fire-and-forget:** scroll, type_text, go_back

---

## SPEC REFERENCE UPDATES

If your spec references old line numbers, use these mappings:

| Component | Old Approx | New Exact |
|-----------|-----------|-----------|
| `_get_browser_state` | ~800 | 911-941 |
| `_scroll` | ~900 | 1017-1032 |
| `_click` | ~700 | 811-863 |
| `_type_text` | ~750 | 865-909 |
| `_extract_content` | ~950 | 943-1015 |
| `_init_browser_session` | ~600 | 582-638 |
| `_retry_with_browser_use_agent` | ~650 | 640-772 |
| `_setup_handlers` | ~200 | 233-505 |
