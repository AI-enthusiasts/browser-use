# Popup Detection Implementation

## Summary
Implemented popup/modal detection and labeling in browser_extract_content MCP tool.

## Changes Made

### 1. ExtractAction (browser_use/tools/views.py)
- Added `skip_json_filtering: bool = Field(default=False)` parameter

### 2. HTMLSerializer (browser_use/dom/serializer/html_serializer.py)
Added popup detection methods:
- `_POPUP_ROLES = frozenset({'dialog', 'alertdialog'})` - constant
- `_is_popup(node)` - checks single node for popup attributes
- `_detect_popups_recursive(node, popups)` - recursive tree traversal
- `detect_popups(root)` - public API, returns list of popup nodes
- `serialize_excluding(node, exclude_nodes)` - serialize with exclusions
- `_serialize_table_children_excluding(...)` - table support for exclusions

Detection criteria:
- `<dialog open>` element
- `role="dialog"` or `role="alertdialog"`
- `aria-modal="true"`

### 3. extract_clean_markdown (browser_use/dom/markdown_extractor.py)
- Added `skip_json_filtering` parameter
- Integrated popup detection (always on)
- Output format when popups detected:
  ```
  --- POPUP/MODAL DETECTED ---
  [popup content]
  
  --- PAGE CONTENT ---
  [main page content]
  ```
- Stats include `popups_detected` count

### 4. _preprocess_markdown_content (browser_use/dom/markdown_extractor.py)
- Added `skip_json_filtering` parameter
- When True: preserves JSON code blocks and large JSON lines

### 5. Tools.extract (browser_use/tools/service.py)
- Extracts and passes `skip_json_filtering` to extract_clean_markdown

### 6. MCP Tool (browser_use/mcp/server.py)
Added parameters to browser_extract_content:
- `skip_json_filtering` (bool, default: false)
- `start_from_char` (int, default: 0)
- `output_schema` (object, optional)

## Tests Created
- `tests/ci/test_popup_detection.py` - 16 tests
- `tests/ci/test_json_filtering.py` - 8 tests

## Usage

### MCP Tool
```python
# Basic extraction
await browser_extract_content(query="Find prices")

# With JSON preservation (for API docs)
await browser_extract_content(
    query="Extract API schema",
    skip_json_filtering=True
)

# With pagination
await browser_extract_content(
    query="Continue reading",
    start_from_char=10000
)

# With structured output
await browser_extract_content(
    query="Extract product info",
    output_schema={"type": "object", "properties": {...}}
)
```

### Agent Tool
```python
action = ExtractAction(
    query="Find login form",
    skip_json_filtering=True,
    start_from_char=0,
    output_schema=None
)
```

## Popup Detection Output Example
When a page has a cookie consent popup:
```
--- POPUP/MODAL DETECTED ---
We use cookies to improve your experience.
[Accept] [Decline]

--- PAGE CONTENT ---
Welcome to our website...
```
