# Minimal API to Build Selector Map from EnhancedDOMTreeNode

## INTENT
Build a selector map (backend_node_id → EnhancedDOMTreeNode) for all interactive elements WITHOUT full DOM serialization. Call DOMTreeSerializer just to get the selector map, then pass that map to HTMLSerializer.

---

## 1. EXACT CONSTRUCTOR SIGNATURE

```python
from browser_use.dom.serializer.serializer import DOMTreeSerializer
from browser_use.dom.views import EnhancedDOMTreeNode, SerializedDOMState

# Constructor signature:
serializer = DOMTreeSerializer(
    root_node: EnhancedDOMTreeNode,
    previous_cached_state: SerializedDOMState | None = None,
    enable_bbox_filtering: bool = True,
    containment_threshold: float | None = None,
    paint_order_filtering: bool = True,
    session_id: str | None = None,
)
```

### Parameters:
- **root_node** (REQUIRED): EnhancedDOMTreeNode from `get_dom_tree()`
- **previous_cached_state** (OPTIONAL): SerializedDOMState from previous call (for caching)
- **enable_bbox_filtering** (default: True): Filter elements by bounding box visibility
- **containment_threshold** (default: None → uses DEFAULT_CONTAINMENT_THRESHOLD = 0.75)
- **paint_order_filtering** (default: True): Remove elements hidden by paint order
- **session_id** (default: None): Session ID for session-specific exclude attributes

---

## 2. HOW TO GET SELECTOR MAP WITH MINIMAL WORK

### Option A: Direct Call to `serialize_accessible_elements()` (RECOMMENDED)

```python
from browser_use.dom.serializer.serializer import DOMTreeSerializer
from browser_use.dom.views import EnhancedDOMTreeNode

# Minimal usage - just get selector map
serializer = DOMTreeSerializer(
    root_node=enhanced_dom_tree,
    previous_cached_state=None,  # No caching
    paint_order_filtering=True,  # Recommended for accuracy
)

# This returns (SerializedDOMState, timing_info)
serialized_dom_state, timing_info = serializer.serialize_accessible_elements()

# Extract selector map
selector_map = serialized_dom_state.selector_map
# Type: dict[int, EnhancedDOMTreeNode]
# Keys: backend_node_id (int)
# Values: EnhancedDOMTreeNode (full node with all attributes, position, etc.)
```

### What `serialize_accessible_elements()` Does:
1. Creates simplified tree (includes clickable detection)
2. Applies paint order filtering (removes hidden elements)
3. Optimizes tree (removes unnecessary parents)
4. Applies bounding box filtering (visibility check)
5. **Assigns interactive indices** → populates `self._selector_map`
6. Returns `SerializedDOMState` with selector_map

**Key:** The selector_map is populated DURING step 5 (`_assign_interactive_indices_and_mark_new_nodes`).

---

## 3. SELECTOR_MAP DATA STRUCTURE

```python
# Type definition (from browser_use/dom/views.py:913)
DOMSelectorMap = dict[int, EnhancedDOMTreeNode]

# Example structure:
selector_map = {
    12345: EnhancedDOMTreeNode(
        backend_node_id=12345,
        tag_name='button',
        attributes={'class': 'submit-btn', 'id': 'submit'},
        snapshot_node=SnapshotNode(bounds=Bounds(x=100, y=200, width=80, height=40)),
        is_visible=True,
        # ... all other EnhancedDOMTreeNode fields
    ),
    12346: EnhancedDOMTreeNode(
        backend_node_id=12346,
        tag_name='input',
        attributes={'type': 'text', 'name': 'search'},
        # ...
    ),
    # ... more interactive elements
}
```

### What's in Each Value (EnhancedDOMTreeNode):
- **backend_node_id**: Unique CDP identifier (int)
- **tag_name**: HTML tag (str)
- **attributes**: Dict of HTML attributes
- **snapshot_node**: SnapshotNode with bounds (x, y, width, height)
- **is_visible**: Boolean visibility flag
- **node_type**: NodeType enum (ELEMENT_NODE, TEXT_NODE, etc.)
- **children_and_shadow_roots**: List of child nodes
- **ax_node**: Accessibility tree node (if available)
- **has_js_click_listener**: Boolean (detected via CDP)
- **is_actually_scrollable**: Boolean
- **_compound_children**: List of compound component info (for selects, sliders, etc.)

---

## 4. CAN `_assign_interactive_indices_and_mark_new_nodes()` BE CALLED STANDALONE?

### Answer: **NO, it's private and requires pre-setup**

```python
# This is PRIVATE (starts with _) and requires:
# 1. self._selector_map to be initialized (empty dict)
# 2. self._interactive_counter to be initialized (= 1)
# 3. self._previous_cached_selector_map to be set (for new element detection)
# 4. self._clickable_cache to be initialized (empty dict)
# 5. Input must be SimplifiedNode (not EnhancedDOMTreeNode)

# DO NOT call directly. Instead, use serialize_accessible_elements()
# which handles all the setup and calls this method internally.
```

### What It Does:
1. Recursively traverses SimplifiedNode tree
2. Calls `_is_interactive_cached()` to check if node is interactive
3. Checks visibility: `node.original_node.is_visible`
4. Handles special cases:
   - File inputs (hidden by design)
   - Shadow DOM form elements (no snapshot_node but functional)
   - Scrollable containers (only if no interactive descendants)
   - Dropdown containers (always indexed)
5. **Populates `self._selector_map[backend_node_id] = node.original_node`**
6. Marks new elements with `node.is_new = True` (for visual highlighting)

### What It Returns:
- **Nothing** (void) — modifies `self._selector_map` in-place
- Sets `node.is_interactive = True` on interactive nodes
- Sets `node.is_new = True` on newly detected elements

---

## 5. CAN `ClickableElementDetector.is_interactive()` BE USED STANDALONE?

### Answer: **YES, it's a static method**

```python
from browser_use.dom.serializer.clickable_elements import ClickableElementDetector
from browser_use.dom.views import EnhancedDOMTreeNode

# Static method - can be called directly
is_interactive = ClickableElementDetector.is_interactive(node: EnhancedDOMTreeNode) -> bool
```

### Signature:
```python
@staticmethod
def is_interactive(node: EnhancedDOMTreeNode) -> bool:
    """Check if this node is clickable/interactive using enhanced scoring."""
```

### What It Checks (in order):
1. **Skip non-element nodes** (TEXT_NODE, etc.)
2. **Skip html/body tags**
3. **JavaScript click listeners** (`node.has_js_click_listener`)
4. **IFRAME/FRAME elements** (if > 100x100px)
5. **Label elements** (with form control descendants)
6. **Span wrappers** (with form control descendants)
7. **Search indicators** (class/id/data attributes containing 'search', 'magnify', etc.)
8. **Accessibility properties** (focusable, editable, checked, expanded, etc.)
9. **Interactive tags**: button, input, select, textarea, a, details, summary, option, optgroup
10. **Interactive attributes**: onclick, onmousedown, tabindex
11. **Interactive ARIA roles**: button, link, menuitem, checkbox, radio, tab, etc.
12. **Accessibility tree roles** (from ax_node)
13. **Icon elements** (10-50px with interactive attributes)
14. **Cursor pointer style** (CSS cursor: pointer)

### Usage Example:
```python
# Check individual nodes
if ClickableElementDetector.is_interactive(node):
    print(f"Node {node.backend_node_id} is interactive")

# Use in custom filtering
interactive_nodes = [
    node for node in all_nodes 
    if ClickableElementDetector.is_interactive(node)
]
```

---

## 6. HOW `extract_clean_markdown` GETS EnhancedDOMTreeNode

### Current Flow:
```python
# From browser_use/dom/markdown_extractor.py:21-149

async def extract_clean_markdown(
    browser_session: 'BrowserSession | None' = None,
    dom_service: DomService | None = None,
    target_id: str | None = None,
    extract_links: bool = False,
    skip_json_filtering: bool = False,
) -> tuple[str, dict[str, Any]]:
    
    # Path 1: Via BrowserSession (tools service)
    if browser_session is not None:
        enhanced_dom_tree = await _get_enhanced_dom_tree_from_browser_session(browser_session)
    
    # Path 2: Via DomService (page actor)
    elif dom_service is not None and target_id is not None:
        enhanced_dom_tree, _ = await dom_service.get_dom_tree(
            target_id=target_id, 
            all_frames=None  # Lazy fetch if needed
        )
    
    # Then uses HTMLSerializer (NOT DOMTreeSerializer)
    html_serializer = HTMLSerializer(extract_links=extract_links)
    page_html = html_serializer.serialize(enhanced_dom_tree)
    
    # Converts to markdown (loses all interactive indices)
    content = markdownify(page_html)
```

### Helper Function:
```python
# From browser_use/dom/markdown_extractor.py
async def _get_enhanced_dom_tree_from_browser_session(
    browser_session: 'BrowserSession'
) -> EnhancedDOMTreeNode:
    """Get enhanced DOM tree from browser session."""
    # Gets DomService from browser_session
    # Calls get_dom_tree() with current target
    # Returns EnhancedDOMTreeNode root
```

---

## 7. HOW `get_browser_state_summary` BUILDS STATE

### Current Flow:
```python
# From browser_use/browser/session.py:1302-1339

async def get_browser_state_summary(
    self,
    include_screenshot: bool = True,
    cached: bool = False,
    include_recent_events: bool = False,
) -> BrowserStateSummary:
    
    # Dispatches BrowserStateRequestEvent
    event = self.event_bus.dispatch(BrowserStateRequestEvent(...))
    
    # Handled by DOMWatchdog.on_BrowserStateRequestEvent()
    result = await event.event_result(raise_if_none=True)
    return result
```

### DOMWatchdog Handler:
```python
# From browser_use/browser/watchdogs/dom_watchdog.py:240-531

async def on_BrowserStateRequestEvent(
    self, 
    event: BrowserStateRequestEvent
) -> BrowserStateSummary:
    
    # 1. Build DOM tree
    previous_state = self.browser_session._cached_browser_state_summary.dom_state
    content = await self._build_dom_tree_without_highlights(previous_state)
    
    # 2. Capture screenshot
    screenshot_b64 = await self._capture_clean_screenshot()
    
    # 3. Get page info, title, tabs
    page_info = await self._get_page_info()
    title = await self.browser_session.get_current_page_title()
    tabs_info = await self.browser_session.get_tabs()
    
    # 4. Return BrowserStateSummary
    return BrowserStateSummary(
        dom_state=content,  # SerializedDOMState with selector_map
        url=page_url,
        title=title,
        tabs=tabs_info,
        screenshot=screenshot_b64,
        page_info=page_info,
        # ...
    )
```

### DOM Tree Building:
```python
# From browser_use/browser/watchdogs/dom_watchdog.py:533-672

async def _build_dom_tree_without_highlights(
    self, 
    previous_state: SerializedDOMState | None = None
) -> SerializedDOMState:
    
    # 1. Get enhanced DOM tree
    self.current_dom_state, self.enhanced_dom_tree, timing_info = \
        await self._dom_service.get_serialized_dom_tree(
            previous_cached_state=previous_state
        )
    
    # 2. Update selector maps
    self.selector_map = self.current_dom_state.selector_map
    self.browser_session.update_cached_selector_map(self.selector_map)
    
    return self.current_dom_state
```

### DomService.get_serialized_dom_tree():
```python
# From browser_use/dom/service.py:1002-1057

async def get_serialized_dom_tree(
    self, 
    previous_cached_state: SerializedDOMState | None = None
) -> tuple[SerializedDOMState, EnhancedDOMTreeNode, dict[str, float]]:
    
    # 1. Get enhanced DOM tree
    enhanced_dom_tree, dom_tree_timing = await self.get_dom_tree(
        target_id=self.browser_session.agent_focus_target_id,
        all_frames=None
    )
    
    # 2. Serialize with DOMTreeSerializer (THIS IS WHERE SELECTOR_MAP IS BUILT)
    serialized_dom_state, serializer_timing = DOMTreeSerializer(
        enhanced_dom_tree, 
        previous_cached_state,
        paint_order_filtering=self.paint_order_filtering,
        session_id=session_id
    ).serialize_accessible_elements()
    
    return serialized_dom_state, enhanced_dom_tree, timing_info
```

---

## 8. MINIMAL CODE SNIPPET: BUILD SELECTOR MAP

```python
from browser_use.dom.serializer.serializer import DOMTreeSerializer
from browser_use.dom.views import EnhancedDOMTreeNode, SerializedDOMState, DOMSelectorMap

async def get_selector_map_minimal(
    enhanced_dom_tree: EnhancedDOMTreeNode,
    previous_cached_state: SerializedDOMState | None = None,
) -> DOMSelectorMap:
    """
    Build selector map from EnhancedDOMTreeNode with minimal work.
    
    Returns:
        dict[int, EnhancedDOMTreeNode] mapping backend_node_id to interactive nodes
    """
    # Create serializer
    serializer = DOMTreeSerializer(
        root_node=enhanced_dom_tree,
        previous_cached_state=previous_cached_state,
        paint_order_filtering=True,  # Recommended
        enable_bbox_filtering=True,  # Recommended
    )
    
    # Get selector map (this is the only thing we need)
    serialized_dom_state, timing_info = serializer.serialize_accessible_elements()
    
    # Extract and return selector map
    return serialized_dom_state.selector_map
```

### Usage:
```python
# Get enhanced DOM tree from browser
enhanced_dom_tree, _ = await dom_service.get_dom_tree(target_id=target_id)

# Get selector map (minimal work)
selector_map = await get_selector_map_minimal(enhanced_dom_tree)

# Now use selector_map with HTMLSerializer
html_serializer = HTMLSerializer()
# ... pass selector_map to HTMLSerializer for rendering with indices
```

---

## 9. INTEGRATION WITH HTMLSerializer

### Current HTMLSerializer (doesn't track indices):
```python
from browser_use.dom.serializer.html_serializer import HTMLSerializer

html_serializer = HTMLSerializer(extract_links=False)
page_html = html_serializer.serialize(enhanced_dom_tree)
# Returns: HTML string WITHOUT interactive indices
```

### Proposed Integration:
```python
# Step 1: Get selector map
selector_map = await get_selector_map_minimal(enhanced_dom_tree)

# Step 2: Pass to HTMLSerializer (if it supported it)
html_serializer = HTMLSerializer(extract_links=False)
page_html = html_serializer.serialize(
    enhanced_dom_tree,
    selector_map=selector_map  # NEW PARAMETER
)
# Returns: HTML with data-interactive-index attributes

# Step 3: Convert to markdown (preserves indices)
from markdownify import markdownify as md
markdown_content = md(page_html)
# Result: Markdown with embedded indices
```

---

## 10. KEY DIFFERENCES: PUBLIC vs PRIVATE API

| Component | Public? | Standalone? | Notes |
|-----------|---------|-------------|-------|
| `DOMTreeSerializer.__init__()` | ✅ Public | ✅ Yes | Constructor is public |
| `DOMTreeSerializer.serialize_accessible_elements()` | ✅ Public | ✅ Yes | Main entry point for selector map |
| `DOMTreeSerializer.serialize_tree()` | ✅ Public | ✅ Yes (static) | Serializes tree to string |
| `DOMTreeSerializer._assign_interactive_indices_and_mark_new_nodes()` | ❌ Private | ❌ No | Requires pre-setup, called by serialize_accessible_elements() |
| `DOMTreeSerializer._is_interactive_cached()` | ❌ Private | ❌ No | Use ClickableElementDetector.is_interactive() instead |
| `ClickableElementDetector.is_interactive()` | ✅ Public | ✅ Yes (static) | Can be used independently |
| `SerializedDOMState` | ✅ Public | ✅ Yes | Dataclass with selector_map field |
| `DOMSelectorMap` | ✅ Public | ✅ Yes | Type alias: dict[int, EnhancedDOMTreeNode] |

---

## 11. PERFORMANCE NOTES

### What `serialize_accessible_elements()` Does (in order):
1. **Create simplified tree** (~5-50ms): Converts EnhancedDOMTreeNode to SimplifiedNode
2. **Paint order filtering** (~1-10ms): Removes hidden elements
3. **Optimize tree** (~1-5ms): Removes unnecessary parents
4. **Bounding box filtering** (~1-10ms): Visibility check
5. **Assign interactive indices** (~5-50ms): Populates selector_map

**Total: ~15-125ms** (depends on DOM size)

### Caching:
- Pass `previous_cached_state` to reuse selector_map from previous call
- Avoids re-detection of interactive elements
- Marks only NEW elements with `is_new = True`

---

## 12. SUMMARY TABLE

| Need | Solution | Code |
|------|----------|------|
| Get selector map | `DOMTreeSerializer.serialize_accessible_elements()` | `serializer = DOMTreeSerializer(root); state, _ = serializer.serialize_accessible_elements(); map = state.selector_map` |
| Check if node is interactive | `ClickableElementDetector.is_interactive()` | `if ClickableElementDetector.is_interactive(node): ...` |
| Get EnhancedDOMTreeNode | `DomService.get_dom_tree()` | `tree, _ = await dom_service.get_dom_tree(target_id)` |
| Get full browser state | `BrowserSession.get_browser_state_summary()` | `state = await session.get_browser_state_summary()` |
| Extract markdown | `extract_clean_markdown()` | `md, stats = await extract_clean_markdown(browser_session=session)` |
| Serialize to HTML | `HTMLSerializer.serialize()` | `html = HTMLSerializer().serialize(tree)` |
| Serialize to DOM tree text | `DOMTreeSerializer.serialize_tree()` | `text = DOMTreeSerializer.serialize_tree(root, include_attrs)` |

---

## CRITICAL INSIGHT

**The selector_map is built DURING `serialize_accessible_elements()`, not before or after.**

You cannot get the selector_map without calling `serialize_accessible_elements()`. However, you don't need to use the returned `SerializedDOMState._root` (the simplified tree) — you only need the `selector_map` field.

This is the minimal API to get what you need:
```python
serializer = DOMTreeSerializer(enhanced_dom_tree)
serialized_state, _ = serializer.serialize_accessible_elements()
selector_map = serialized_state.selector_map  # ← This is what you need
```
