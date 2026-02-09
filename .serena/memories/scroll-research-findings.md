# Scroll Functionality Research - Complete Findings

## 1. MCP Tool: browser_scroll

**Location:** `browser_use/mcp/server.py` lines 334-347, 557-558, 999-1014

**Current Parameters:**
- `direction` (string, enum: 'up'|'down', default: 'down')

**Current Return:**
- Simple string: "Scrolled {direction}"

**Implementation:**
- Hardcoded scroll amount: **500 pixels**
- No distance control
- No element-specific scrolling
- No scroll position feedback
- Dispatches `ScrollEvent(direction=direction, amount=500)`

---

## 2. Agent's Scroll Action: ScrollAction

**Location:** `browser_use/tools/views.py` lines 113-116

**Parameters:**
```python
class ScrollAction(BaseModel):
    down: bool = Field(default=True, description='down=True=scroll down, down=False scroll up')
    pages: float = Field(default=1.0, description='0.5=half page, 1=full page, 10=to bottom/top')
    index: int | None = Field(default=None, description='Optional element index to scroll within specific element')
```

**Key Capabilities:**
- ✅ Distance control via `pages` parameter (0.5-10.0)
- ✅ Element-specific scrolling via `index` parameter
- ✅ Viewport-aware (detects actual viewport height via CDP)
- ✅ Multi-page scrolling with sequential execution
- ✅ Fallback to 1000px if viewport detection fails

**Implementation:** `browser_use/tools/service.py` lines 1248-1351
- Gets viewport height from CDP: `Page.getLayoutMetrics()`
- Calculates pixels = pages * viewport_height
- For multi-page scrolls: loops with 0.15s delay between each
- Returns ActionResult with memory: "Scrolled {direction} {target} {pages} pages"

---

## 3. Underlying Scroll Implementation: ScrollEvent

**Location:** `browser_use/browser/events.py` lines 158-165

```python
class ScrollEvent(ElementSelectedEvent[None]):
    direction: Literal['up', 'down', 'left', 'right']
    amount: int  # pixels
    node: 'EnhancedDOMTreeNode | None' = None  # None means scroll page
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_ScrollEvent', 8.0))
```

**Supports:**
- ✅ Element-specific scrolling (via `node` parameter)
- ✅ Pixel-level distance control (via `amount` parameter)
- ✅ 4 directions: up, down, left, right

---

## 4. Watchdog Implementation: _scroll_with_cdp_gesture

**Location:** `browser_use/browser/watchdogs/default_action_watchdog.py` lines 2114-2165

**Method:** CDP Input.synthesizeScrollGesture
- Gets viewport dimensions from cached value or CDP
- Calculates center of viewport
- Converts pixels to yDistance (negative = down, positive = up)
- Uses speed=50000 (pixels/second) for near-instant scrolling
- Fallback to JavaScript if CDP fails

---

## 5. Element-Specific Scroll: _scroll_element_container

**Location:** `browser_use/browser/watchdogs/default_action_watchdog.py` lines 2167-2255

**Capabilities:**
- ✅ Scrolls specific elements (not just page)
- ✅ Special handling for iframes (scrolls content document)
- ✅ Uses mouseWheel event at element center
- ✅ Gets element bounds via DOM.getBoxModel()
- ✅ Returns success/failure boolean

**For iframes:**
- Resolves node to objectId
- Calls JavaScript function on iframe to scroll contentDocument
- Returns: {success, oldScrollTop, newScrollTop, scrolled}

---

## 6. Find Text Action: find_text

**Location:** `browser_use/tools/service.py` lines 1373-1392

**Implementation:**
- Dispatches `ScrollToTextEvent(text=text)`
- Returns ActionResult with memory: "Scrolled to text: {text}"
- Raises exception if text not found

**ScrollToTextEvent:**
- Location: `browser_use/browser/events.py` lines 279-285
- Parameters: text (required), direction (default: 'down')
- Timeout: 15 seconds

---

## 7. Scroll Position Information Available

**PageInfo Model** (`browser_use/browser/views.py` lines 43-62):
```python
class PageInfo(BaseModel):
    viewport_width: int
    viewport_height: int
    page_width: int
    page_height: int
    scroll_x: int
    scroll_y: int
    pixels_above: int
    pixels_below: int
    pixels_left: int
    pixels_right: int
```

**EnhancedDOMTreeNode.scroll_info** (`browser_use/dom/views.py` lines 716-785):
- For scrollable elements, returns dict with:
  - scroll_top, scroll_left
  - scrollable_height, scrollable_width
  - visible_height, visible_width
  - content_above, content_below, content_left, content_right
  - vertical_scroll_percentage, horizontal_scroll_percentage
  - pages_above, pages_below, total_pages
  - can_scroll_up, can_scroll_down, can_scroll_left, can_scroll_right

---

## 8. CDP Scroll Methods Available

**Mouse Wheel Event:**
```python
Input.dispatchMouseEvent(
    type='mouseWheel',
    x: float, y: float,
    deltaX: int, deltaY: int
)
```

**Synthesize Scroll Gesture:**
```python
Input.synthesizeScrollGesture(
    x: float, y: float,
    xDistance: int, yDistance: int,
    speed: int  # pixels/second
)
```

**JavaScript Fallback:**
```javascript
window.scrollBy(deltaX, deltaY)
element.scrollTop += pixels
```

---

## 9. Gap Analysis: MCP vs Agent

| Feature | MCP Tool | Agent Action | Gap |
|---------|----------|--------------|-----|
| Distance control | ❌ Hardcoded 500px | ✅ pages parameter | MCP missing |
| Element-specific | ❌ Page only | ✅ index parameter | MCP missing |
| Scroll feedback | ❌ No info | ✅ ActionResult | MCP missing |
| Viewport awareness | ❌ No | ✅ Yes (CDP) | MCP missing |
| Multi-page support | ❌ No | ✅ Yes (sequential) | MCP missing |
| Direction options | ✅ up/down | ✅ up/down | Equal |
| Find text | ❌ No | ✅ Yes | MCP missing |

---

## 10. Concrete Enhancement Proposal

### Enhanced MCP browser_scroll Tool

**New Parameters:**
```json
{
  "direction": "string (up|down|left|right, default: down)",
  "distance": "number (pixels, default: 500)",
  "pages": "number (0.5-10.0, alternative to distance)",
  "element_index": "integer (optional, for element-specific scroll)",
  "include_position": "boolean (default: false, return scroll position)"
}
```

**Enhanced Return:**
```json
{
  "status": "success|error",
  "message": "Scrolled down 500px",
  "scroll_position": {
    "scroll_x": 0,
    "scroll_y": 1500,
    "pixels_above": 1500,
    "pixels_below": 2000,
    "scroll_percentage": 42.9
  },
  "element_info": {
    "index": 5,
    "scrollable": true,
    "can_scroll_further": true
  }
}
```

**Implementation Strategy:**
1. Reuse Agent's ScrollAction logic
2. Add optional scroll position feedback
3. Support element-specific scrolling via index
4. Maintain backward compatibility (distance defaults to 500px)
5. Return structured ActionResult instead of plain string

---

## 11. Key Code Locations for Implementation

- **MCP Tool Definition:** `browser_use/mcp/server.py:334-347` (tool schema)
- **MCP Tool Handler:** `browser_use/mcp/server.py:557-558` (dispatch)
- **MCP Tool Implementation:** `browser_use/mcp/server.py:999-1014` (method)
- **Agent Action:** `browser_use/tools/service.py:1248-1351` (reference implementation)
- **ScrollAction Model:** `browser_use/tools/views.py:113-116` (reuse/adapt)
- **ScrollEvent:** `browser_use/browser/events.py:158-165` (underlying event)
- **Watchdog Handlers:** `browser_use/browser/watchdogs/default_action_watchdog.py:2114-2255`
