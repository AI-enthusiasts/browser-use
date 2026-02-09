# Interactive Elements + Markdown Extraction Architecture

## INTENT SUMMARY
User needs to understand how to make `browser_extract_content` return interactive elements (buttons, links, inputs) with their clickable indices alongside page content. Currently extract returns only markdown text — LLM can read but can't act. Goal: merge the two views (interactive elements + markdown).

---

## 1. HOW INTERACTIVE ELEMENTS ARE INDEXED

### Data Flow: DOM → Interactive Elements List

**File:** `browser_use/dom/serializer/serializer.py`
**Class:** `DOMTreeSerializer`
**Key Method:** `_assign_interactive_indices_and_mark_new_nodes()` (lines 616-726)

#### Indexing Mechanism:
1. **Detection Phase** (`_is_interactive_cached()`):
   - Uses `ClickableElementDetector.is_interactive()` to identify interactive nodes
   - Checks: tag names (button, input, a, select, etc.), ARIA roles, event listeners, accessibility properties
   - File: `browser_use/dom/serializer/clickable_elements.py`

2. **Index Assignment**:
   - **NOT sequential indices** — instead uses `backend_node_id` from CDP (Chrome DevTools Protocol)
   - Stored in `self._selector_map[backend_node_id] = node` (line 667)
   - `backend_node_id` is a unique identifier from Chrome's DOM snapshot
   - Counter `self._interactive_counter` tracks total interactive elements

3. **Visibility Filtering**:
   - Only indexes elements that are:
     - Visible (`node.original_node.is_visible`)
     - OR file inputs (hidden by design)
     - OR shadow DOM form elements (no snapshot_node but functional)
   - Scrollable containers indexed only if they have no interactive descendants

4. **Output Format in Serialized Tree**:
   - Interactive elements marked with `[backend_node_id]` in text representation
   - Example: `[12345]<button class="submit">Click me</button>`
   - New elements marked with `*` prefix: `*[12345]<button>...`

### Data Structure: DOMSelectorMap
**File:** `browser_use/dom/views.py`
**Type:** `DOMSelectorMap = dict[int, EnhancedDOMTreeNode]`
- Key: `backend_node_id` (int)
- Value: `EnhancedDOMTreeNode` (full node with all attributes, position, etc.)

### Where Indices Are Used:
- **Agent clicks elements by backend_node_id**, not by sequential index
- `browser_session.get_dom_element_by_index()` → looks up in selector_map
- `browser_session.get_element_by_index()` → CDP call to get element

---

## 2. WHAT THE AGENT SEES IN ITS PROMPT

### File:** `browser_use/agent/prompts.py`
**Class:** `AgentMessagePrompt`
**Key Method:** `_get_browser_state_description()` (lines 217-317)

### Agent Prompt Format:

```
<page_stats>
X links, Y interactive, Z iframes, ... total elements
</page_stats>

Current tab: [tab_id]
Available tabs:
Tab [id]: [url] - [title]

<page_info>
X above, Y below
</page_info>

Interactive elements:
[12345]<button class="submit">Click me</button>
[12346]<input type="text" name="search" />
[12347]<a href="/page">Link text</a>
...
```

### What's Included:
1. **Page statistics** (counts only, no indices)
2. **Tab information** (URLs, titles)
3. **Page scroll info** (pixels above/below viewport)
4. **DOM tree with interactive indices** — from `browser_state.dom_state.llm_representation()`

### What's NOT Included:
- **Markdown content** — NOT in the browser state message
- **Link targets** — only in DOM tree representation
- **Form field values** — only structure, not current values

### How DOM Tree is Generated:
**File:** `browser_use/dom/views.py`
**Method:** `SerializedDOMState.llm_representation()` (lines 935-948)
- Calls `DOMTreeSerializer.serialize_tree()` with interactive indices
- Includes attributes specified in `include_attributes` parameter
- Shows text content for visible text nodes
- Marks shadow DOM boundaries

---

## 3. WHERE THE TWO PATHS DIVERGE

### Path 1: DOM → Interactive Elements (for Agent)
```
Enhanced DOM Tree
    ↓
DOMTreeSerializer._assign_interactive_indices_and_mark_new_nodes()
    ↓
Selector Map: {backend_node_id → EnhancedDOMTreeNode}
    ↓
DOMTreeSerializer.serialize_tree()
    ↓
Text representation with [backend_node_id] markers
    ↓
Agent sees: "[12345]<button>Click</button>"
```

**Key:** Uses `backend_node_id` as the index, preserves full DOM structure

### Path 2: DOM → Markdown (for Extract Tool)
```
Enhanced DOM Tree
    ↓
HTMLSerializer.serialize() → HTML string
    ↓
markdownify() → Markdown
    ↓
_preprocess_markdown_content() → Clean markdown
    ↓
extract_clean_markdown() returns: (markdown_text, stats)
    ↓
Tool returns: "# Heading\n\nParagraph text\n\n- List item"
```

**Key:** Converts to HTML first, then markdown. **Loses all interactive element indices.**

### Divergence Point:
- **Interactive path:** Preserves DOM structure + indices
- **Markdown path:** Converts to semantic text, loses structure + indices

**File:** `browser_use/dom/markdown_extractor.py` (lines 21-149)
- Uses `HTMLSerializer` (not `DOMTreeSerializer`)
- No index assignment
- No selector map creation

---

## 4. CURRENT EXTRACT FLOW (MCP Tool)

**File:** `browser_use/tools/service.py` (or MCP server)
**Tool:** `browser_extract_content`

Current implementation:
1. Takes query (what to extract)
2. Calls `extract_clean_markdown()` → returns markdown only
3. Returns markdown text to LLM

**Problem:** LLM reads content but can't click anything because no indices provided.

---

## 5. RECOMMENDATION: WHERE TO MERGE

### Option A: Merge in HTMLSerializer (RECOMMENDED)
**Pros:**
- Minimal changes to existing flow
- Reuses existing serialization logic
- Can preserve both HTML structure AND indices

**Cons:**
- HTMLSerializer currently doesn't track indices
- Would need to add index tracking during HTML serialization

**Implementation:**
1. Modify `HTMLSerializer.serialize()` to track `backend_node_id` for interactive elements
2. Add index markers to HTML output: `<button data-interactive-index="12345">Click</button>`
3. markdownify preserves data attributes → markdown includes indices
4. Extract tool returns markdown with embedded indices

### Option B: Merge in extract_clean_markdown (GOOD)
**Pros:**
- Centralized extraction logic
- Can build selector map alongside markdown

**Cons:**
- Duplicates index assignment logic
- More complex preprocessing

**Implementation:**
1. In `extract_clean_markdown()`, also build selector map
2. Return tuple: (markdown, selector_map, stats)
3. Extract tool formats both for LLM

### Option C: Append After Extraction (SIMPLEST)
**Pros:**
- No changes to existing serialization
- Cleanest separation of concerns

**Cons:**
- Duplicates DOM traversal
- Less integrated with markdown content

**Implementation:**
1. Extract tool calls `extract_clean_markdown()` → markdown
2. Extract tool calls `get_browser_state_summary()` → selector_map
3. Format both together for LLM

### Option D: Create New Serializer (MOST FLEXIBLE)
**Pros:**
- Purpose-built for extraction with indices
- Can optimize for LLM readability

**Cons:**
- New code path to maintain
- Duplicates serialization logic

**Implementation:**
1. Create `ExtractSerializer` that combines:
   - Markdown content from HTMLSerializer
   - Interactive indices from DOMTreeSerializer
2. Returns: `(markdown_with_indices, selector_map)`

---

## 6. CURRENT AGENT FLOW (For Reference)

1. **Agent step starts** → `_prepare_context()`
2. **Get browser state** → `get_browser_state_summary()`
3. **Build DOM tree** → `DOMTreeSerializer.serialize_tree()` with indices
4. **Format for LLM** → `AgentMessagePrompt._get_browser_state_description()`
5. **Agent sees:** DOM tree with `[backend_node_id]` markers
6. **Agent outputs:** `{"action": [{"click": {"backend_node_id": 12345}}]}`
7. **Browser executes:** `get_dom_element_by_index(12345)` → clicks element

---

## 7. KEY FILES SUMMARY

| File | Purpose | Key Classes/Functions |
|------|---------|----------------------|
| `browser_use/dom/serializer/serializer.py` | DOM tree serialization with indices | `DOMTreeSerializer`, `_assign_interactive_indices_and_mark_new_nodes()` |
| `browser_use/dom/serializer/clickable_elements.py` | Interactive element detection | `ClickableElementDetector.is_interactive()` |
| `browser_use/dom/serializer/html_serializer.py` | HTML serialization (no indices) | `HTMLSerializer.serialize()` |
| `browser_use/dom/markdown_extractor.py` | Markdown extraction | `extract_clean_markdown()` |
| `browser_use/dom/views.py` | Data structures | `SerializedDOMState`, `DOMSelectorMap` |
| `browser_use/agent/prompts.py` | Agent prompt formatting | `AgentMessagePrompt._get_browser_state_description()` |
| `browser_use/browser/session.py` | Browser state building | `get_browser_state_summary()` |

---

## 8. IMPLEMENTATION STATUS: COMPLETE

### What Was Implemented (Hybrid Approach):

1. **HTMLSerializer** (`browser_use/dom/serializer/html_serializer.py`):
   - Added `selector_map: dict[int, Any] | None = None` to `__init__`
   - Added `_get_interactive_marker(node)` helper that returns tag-specific markers
   - Markers injected after void elements (`/>`) and after closing tags (`</{tag}>`)
   - Marker format: `[btn:12345]`, `[link:12345]`, `[input:12345 type=text]`, `[select:12345]`, `[textarea:12345]`, `[interactive:12345]`

2. **extract_clean_markdown** (`browser_use/dom/markdown_extractor.py`):
   - Added `include_interactive: bool = False` parameter
   - When True: builds selector_map via `DOMTreeSerializer.serialize_accessible_elements()`
   - Passes selector_map to HTMLSerializer
   - Adds `stats['interactive_elements']` count

3. **tools/service.py**:
   - Extract action always passes `include_interactive=True`

### Key Design Decisions:
- Text markers (not data attributes) because `_serialize_attributes()` skips all `data-*` attributes
- Markers placed AFTER closing tags so markdownify preserves them as plain text
- Uses `backend_node_id` as index — same as what `browser_click` uses
- Default behavior unchanged when `include_interactive=False`
