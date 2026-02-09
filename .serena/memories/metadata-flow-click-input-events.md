# Metadata Flow: Click & Input Events → ActionResult

## INTENT
Understand how `click_metadata` and `input_metadata` are produced by event handlers and returned through the Agent's ActionResult, so MCP tools can surface this information.

---

## 1. EVENT CLASSES (browser_use/browser/events.py)

### ClickElementEvent
**Location:** `browser_use/browser/events.py:125-132`

```python
class ClickElementEvent(ElementSelectedEvent[dict[str, Any] | None]):
    """Click an element."""
    
    node: 'EnhancedDOMTreeNode'
    button: Literal['left', 'right', 'middle'] = 'left'
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ClickElementEvent', 15.0))
```

**Event Result Type:** `dict[str, Any] | None` — The handler returns a dict with click metadata or None.

### TypeTextEvent
**Location:** `browser_use/browser/events.py:147-154`

```python
class TypeTextEvent(ElementSelectedEvent[dict | None]):
    """Type text into an element."""
    
    node: 'EnhancedDOMTreeNode'
    text: str
    clear: bool = True
    is_sensitive: bool = False
    sensitive_key_name: str | None = None
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_TypeTextEvent', 60.0))
```

**Event Result Type:** `dict | None` — The handler returns a dict with input metadata or None.

### ScrollToTextEvent
**Location:** `browser_use/browser/events.py:280-285`

```python
class ScrollToTextEvent(BaseEvent[None]):
    """Scroll to specific text on the page. Raises exception if text not found."""
    
    text: str
    direction: Literal['up', 'down'] = 'down'
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScrollToTextEvent', 15.0))
```

**Event Result Type:** `None` — No metadata returned.

---

## 2. EVENT HANDLERS (browser_use/browser/watchdogs/default_action_watchdog.py)

### on_ClickElementEvent Handler
**Location:** `browser_use/browser/watchdogs/default_action_watchdog.py:335-386`

**Handler Signature:**
```python
@observe_debug(ignore_input=True, ignore_output=True, name='click_element_event')
async def on_ClickElementEvent(self, event: ClickElementEvent) -> dict | None:
```

**Key Flow:**
1. **Line 345-349:** Check if session is alive (target_id exists)
2. **Line 352-354:** Check if element is file input → return `{'validation_error': msg}`
3. **Line 357-365:** Check if print-related element → call `_handle_print_button_click()` → return PDF metadata
4. **Line 368:** Call `_execute_click_with_download_detection()` → returns click_metadata dict
5. **Line 370-372:** Check for validation errors in metadata
6. **Line 375-378:** Build success message with element description
7. **Line 380:** Return click_metadata dict

**Metadata Structure Returned:**
```python
# From _execute_click_with_download_detection (lines 43-221):
{
    'validation_error': str,  # If file input, print button, etc.
    'download': {
        'path': str,
        'file_name': str,
        'file_size': int,
        'file_type': str | None,
        'mime_type': str | None,
    },
    'download_in_progress': {
        'file_name': str,
        'received_bytes': int,
        'total_bytes': int,
        'state': str,
        'message': str,
    },
    'download_timeout': {
        'file_name': str,
        'received_bytes': int,
        'total_bytes': int,
        'message': str,
    },
    'pdf_generated': bool,  # From _handle_print_button_click
    'path': str,  # PDF path if generated
}
```

**Key Implementation Details:**
- **Lines 43-221:** `_execute_click_with_download_detection()` wraps the click coroutine and monitors for downloads
- **Lines 60-80:** Download callbacks (on_download_start, on_download_progress, on_download_complete) populate download_info dict
- **Lines 100-120:** If download completes, merges into click_metadata['download']
- **Lines 121-180:** If download times out, creates 'download_in_progress' or 'download_timeout' keys with status

### on_TypeTextEvent Handler
**Location:** `browser_use/browser/watchdogs/default_action_watchdog.py:450-510`

**Handler Signature:**
```python
async def on_TypeTextEvent(self, event: TypeTextEvent) -> dict | None:
```

**Key Flow:**
1. **Line 455-456:** Get element node and index
2. **Line 459-468:** If index is 0 or falsy → type to page (no coordinates) → return None
3. **Line 469-490:** Else → call `_input_text_element_node_impl()` → returns input_metadata dict
4. **Line 491-509:** Fallback to page typing if element typing fails → return None

**Metadata Structure Returned:**
```python
# From _input_text_element_node_impl (lines 1645-1998):
{
    'input_x': float,  # Center X coordinate of element
    'input_y': float,  # Center Y coordinate of element
    'actual_value': str,  # The actual value in field after typing
}
```

**Key Implementation Details:**
- **Lines 1680-1710:** Get element coordinates using `get_element_coordinates()` → center_x, center_y
- **Lines 1711-1720:** Check for occlusion; if occluded, skip coordinates
- **Lines 1722-1724:** Store coordinates in input_coordinates dict
- **Lines 1850-1870:** Read back actual value from field using Runtime.callFunctionOn()
- **Lines 1872-1880:** If actual_value differs from typed text, log warning (concatenation detection)
- **Lines 1881-1920:** Auto-retry with direct value assignment if concatenation detected
- **Line 1998:** Return input_coordinates dict (may contain input_x, input_y, actual_value)

---

## 3. AGENT TOOL INTEGRATION (browser_use/tools/service.py)

### _click_by_index Method
**Location:** `browser_use/tools/service.py:565-626`

**Key Flow:**
```python
async def _click_by_index(
    params: ClickElementAction | ClickElementActionIndexOnly, 
    browser_session: BrowserSession
) -> ActionResult:
```

1. **Line 575-577:** Get element node from selector map
2. **Line 582:** Get element description using `get_click_description(node)`
3. **Line 585:** Capture tab IDs before click
4. **Line 591-593:** Dispatch ClickElementEvent and await handler
5. **Line 595:** Get click_metadata from event.event_result()
6. **Line 597-608:** Check for validation errors (select, file input)
7. **Line 611-612:** Build memory with element description + new tab detection
8. **Line 615-619:** Return ActionResult with metadata

**ActionResult Structure:**
```python
ActionResult(
    extracted_content=memory,  # "Clicked button: Submit" + new tab info
    metadata=click_metadata if isinstance(click_metadata, dict) else None,
)
```

**Line 595 Critical:** `click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)`
- This awaits the handler and extracts the return value (the dict)
- `raise_if_none=False` allows None returns (no metadata)

### input Method
**Location:** `browser_use/tools/service.py:639-719`

**Key Flow:**
```python
async def input(
    params: InputTextAction,
    browser_session: BrowserSession,
    has_sensitive_data: bool = False,
    sensitive_data: dict[str, str | dict[str, str]] | None = None,
):
```

1. **Line 651-653:** Get element node from selector map
2. **Line 659-673:** Dispatch TypeTextEvent with sensitive data handling
3. **Line 674:** Get input_metadata from event.event_result()
4. **Line 676-690:** Build message with sensitive data protection
5. **Line 692-695:** Extract actual_value from metadata (pop it out)
6. **Line 697-699:** Check for value mismatch and add warning to message
7. **Line 701-708:** Check for autocomplete field and add delay
8. **Line 711-715:** Return ActionResult with metadata

**ActionResult Structure:**
```python
ActionResult(
    extracted_content=msg,  # "Typed 'password'" + warnings
    long_term_memory=msg,
    metadata=input_metadata if isinstance(input_metadata, dict) else None,
)
```

**Line 674 Critical:** `input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)`
- This awaits the handler and extracts the return value (the dict)
- `raise_if_none=False` allows None returns (no metadata)

---

## 4. ACTIONRESULT CLASS (browser_use/agent/views.py:312-353)

**Location:** `browser_use/agent/views.py:312-353`

```python
class ActionResult(BaseModel):
    # For done action
    is_done: bool | None = False
    success: bool | None = None
    
    # Error handling
    error: str | None = None
    
    # Files
    attachments: list[str] | None = None
    
    # Images (base64 encoded)
    images: list[dict[str, Any]] | None = None
    
    # Memory
    long_term_memory: str | None = None
    extracted_content: str | None = None
    include_extracted_content_only_once: bool = False
    
    # ← METADATA FIELD (for observability)
    metadata: dict | None = None
    
    # Deprecated
    include_in_memory: bool = False
```

**Key Field:** `metadata: dict | None = None`
- Designed for observability data (click coordinates, input coordinates, etc.)
- Not included in long_term_memory automatically
- Available for MCP tools to surface

---

## 5. DATA FLOW DIAGRAM

```
Agent Task
    ↓
_click_by_index() / input()
    ↓
Dispatch ClickElementEvent / TypeTextEvent
    ↓
on_ClickElementEvent() / on_TypeTextEvent() [Handler]
    ↓
_execute_click_with_download_detection() / _input_text_element_node_impl()
    ↓
Produce metadata dict:
  - click_metadata: {validation_error, download, download_in_progress, download_timeout, pdf_generated, path}
  - input_metadata: {input_x, input_y, actual_value}
    ↓
Return dict from handler
    ↓
event.event_result() extracts dict
    ↓
ActionResult(extracted_content=msg, metadata=metadata_dict)
    ↓
Agent receives ActionResult with metadata field populated
```

---

## 6. MCP TOOL INTEGRATION POINTS

### Current MCP Implementation (browser_use/mcp/server.py)
- **_click method (lines 804-856):** Returns simple string, ignores click_metadata
- **_type_text method (lines 858-902):** Returns simple string, ignores input_metadata

### What's Available But Not Surfaced
1. **From click_metadata:**
   - Download information (file_name, file_size, path)
   - Download progress (in_progress, timeout status)
   - PDF generation status
   - Validation errors (file input, print button, select)

2. **From input_metadata:**
   - Input coordinates (input_x, input_y)
   - Actual value that was set (for verification)
   - Value mismatch warnings (autocomplete, reformatting)

### Recommended MCP Enhancement
```python
# In _click method:
click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
if isinstance(click_metadata, dict):
    if 'download' in click_metadata:
        return f"Clicked element {index} - downloaded {click_metadata['download']['file_name']}"
    if 'pdf_generated' in click_metadata:
        return f"Clicked element {index} - generated PDF: {click_metadata['path']}"
    if 'validation_error' in click_metadata:
        return f"Error: {click_metadata['validation_error']}"
return f"Clicked element {index}"

# In _type_text method:
input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
if isinstance(input_metadata, dict):
    actual_value = input_metadata.get('actual_value')
    if actual_value and actual_value != text:
        return f"Typed '{text}' into element {index} - actual value: '{actual_value}'"
return f"Typed '{text}' into element {index}"
```

---

## 7. KEY LINE NUMBERS SUMMARY

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| ClickElementEvent | events.py | 125-132 | Event class definition |
| TypeTextEvent | events.py | 147-154 | Event class definition |
| ScrollToTextEvent | events.py | 280-285 | Event class definition |
| on_ClickElementEvent | default_action_watchdog.py | 335-386 | Handler that produces click_metadata |
| _execute_click_with_download_detection | default_action_watchdog.py | 43-221 | Produces download metadata |
| on_TypeTextEvent | default_action_watchdog.py | 450-510 | Handler that produces input_metadata |
| _input_text_element_node_impl | default_action_watchdog.py | 1645-1998 | Produces input coordinates & actual_value |
| _click_by_index | service.py | 565-626 | Agent tool that extracts click_metadata |
| input | service.py | 639-719 | Agent tool that extracts input_metadata |
| ActionResult | views.py | 312-353 | Result class with metadata field |

---

## 8. CRITICAL EXTRACTION POINTS

### For MCP Click Tool
**File:** `browser_use/mcp/server.py` (lines 804-856)

Current code:
```python
event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
await event
# ← MISSING: Extract click_metadata here
return f'Clicked element {index}'
```

Should be:
```python
event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
await event
click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
# ← NOW: click_metadata contains download info, validation errors, etc.
if isinstance(click_metadata, dict):
    # Surface metadata in return string
    ...
return f'Clicked element {index}'
```

### For MCP Type Tool
**File:** `browser_use/mcp/server.py` (lines 858-902)

Current code:
```python
event = browser_session.event_bus.dispatch(TypeTextEvent(node=node, text=text, ...))
await event
# ← MISSING: Extract input_metadata here
return f"Typed '{text}' into element {index}"
```

Should be:
```python
event = browser_session.event_bus.dispatch(TypeTextEvent(node=node, text=text, ...))
await event
input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
# ← NOW: input_metadata contains coordinates and actual_value
if isinstance(input_metadata, dict):
    # Surface metadata in return string
    ...
return f"Typed '{text}' into element {index}"
```

---

## 9. METADATA FIELD USAGE IN AGENT

The Agent's ActionResult.metadata field is designed for exactly this use case:
- **Not included in long_term_memory** (observability only)
- **Available for downstream processing** (MCP tools, logging, etc.)
- **Type:** `dict | None` (flexible structure)

Example from Agent code (service.py:615-619):
```python
return ActionResult(
    extracted_content=memory,
    metadata=click_metadata if isinstance(click_metadata, dict) else None,
)
```

This pattern should be replicated in MCP tools to surface metadata.
