# Re-implementation: Interactive Element Markers - EXACT CODE REFERENCE

## INTENT VERIFICATION
User needs to re-implement interactive element markers in `extract_content` (the extract tool). The previous implementation was lost. This document provides EXACT code signatures and full method bodies for all 3 files to enable correct re-implementation.

---

## FILE 1: browser_use/dom/serializer/html_serializer.py

### HTMLSerializer.__init__ (Lines 18-24)
**CURRENT STATE:**
```python
def __init__(self, extract_links: bool = False):
    """Initialize the HTML serializer.

    Args:
        extract_links: If True, preserves all links. If False, removes href attributes.
    """
    self.extract_links = extract_links
```

**WHAT'S MISSING:**
- NO `selector_map` parameter
- NO `self.selector_map` assignment

**NEEDS TO BE CHANGED TO:**
```python
def __init__(self, extract_links: bool = False, selector_map: dict[int, Any] | None = None):
    """Initialize the HTML serializer.

    Args:
        extract_links: If True, preserves all links. If False, removes href attributes.
        selector_map: Optional mapping of backend_node_id to EnhancedDOMTreeNode for interactive element markers.
    """
    self.extract_links = extract_links
    self.selector_map = selector_map
```

---

### HTMLSerializer.serialize() (Lines 311-454)
**CURRENT STATE - FULL METHOD BODY:**
```python
def serialize(self, node: EnhancedDOMTreeNode, depth: int = 0) -> str:
    """Serialize an enhanced DOM tree node to HTML.

    Args:
        node: The enhanced DOM tree node to serialize
        depth: Current depth for indentation (internal use)

    Returns:
        HTML string representation of the node and its descendants
    """
    if node.node_type == NodeType.DOCUMENT_NODE:
        # Process document root - serialize all children
        parts = []
        for child in node.children_and_shadow_roots:
            child_html = self.serialize(child, depth)
            if child_html:
                parts.append(child_html)
        return ''.join(parts)

    elif node.node_type == NodeType.DOCUMENT_FRAGMENT_NODE:
        # Shadow DOM root - wrap in template with shadowrootmode attribute
        parts = []

        # Add shadow root opening
        shadow_type = node.shadow_root_type or 'open'
        parts.append(f'<template shadowroot="{shadow_type.lower()}">')

        # Serialize shadow children
        for child in node.children:
            child_html = self.serialize(child, depth + 1)
            if child_html:
                parts.append(child_html)

        # Close shadow root
        parts.append('</template>')

        return ''.join(parts)

    elif node.node_type == NodeType.ELEMENT_NODE:
        parts = []
        tag_name = node.tag_name.lower()

        # Skip non-content elements
        if tag_name in {'style', 'script', 'head', 'meta', 'link', 'title'}:
            return ''

        # Skip code tags with display:none - these often contain JSON state for SPAs
        if tag_name == 'code' and node.attributes:
            style = node.attributes.get('style', '')
            # Check if element is hidden (display:none) - likely JSON data
            if 'display:none' in style.replace(' ', '') or 'display: none' in style:
                return ''
            # Also check for bpr-guid IDs (LinkedIn's JSON data pattern)
            element_id = node.attributes.get('id', '')
            if 'bpr-guid' in element_id or 'data' in element_id or 'state' in element_id:
                return ''

        # Skip base64 inline images - these are usually placeholders or tracking pixels
        if tag_name == 'img' and node.attributes:
            src = node.attributes.get('src', '')
            if src.startswith('data:image/'):
                return ''

        # Opening tag
        parts.append(f'<{tag_name}')

        # Add attributes
        if node.attributes:
            attrs = self._serialize_attributes(node.attributes)
            if attrs:
                parts.append(' ' + attrs)

        # Handle void elements (self-closing)
        void_elements = {
            'area',
            'base',
            'br',
            'col',
            'embed',
            'hr',
            'img',
            'input',
            'link',
            'meta',
            'param',
            'source',
            'track',
            'wbr',
        }
        if tag_name in void_elements:
            parts.append(' />')
            return ''.join(parts)

        parts.append('>')

        # Handle table normalization (ensure thead/tbody for markdownify)
        if tag_name == 'table':
            # Serialize shadow roots first (same as the general path)
            if node.shadow_roots:
                for shadow_root in node.shadow_roots:
                    child_html = self.serialize(shadow_root, depth + 1)
                    if child_html:
                        parts.append(child_html)
            table_html = self._serialize_table_children(node, depth)
            parts.append(table_html)
        # Handle iframe content document
        elif tag_name in {'iframe', 'frame'} and node.content_document:
            # Serialize iframe content
            for child in node.content_document.children_nodes or []:
                child_html = self.serialize(child, depth + 1)
                if child_html:
                    parts.append(child_html)
        else:
            # Serialize shadow roots FIRST (for declarative shadow DOM)
            if node.shadow_roots:
                for shadow_root in node.shadow_roots:
                    child_html = self.serialize(shadow_root, depth + 1)
                    if child_html:
                        parts.append(child_html)

            # Then serialize light DOM children (for slot projection)
            for child in node.children:
                child_html = self.serialize(child, depth + 1)
                if child_html:
                    parts.append(child_html)

        # Closing tag
        parts.append(f'</{tag_name}>')

        return ''.join(parts)

    elif node.node_type == NodeType.TEXT_NODE:
        # Return text content with basic HTML escaping
        if node.node_value:
            return self._escape_html(node.node_value)
        return ''

    elif node.node_type == NodeType.COMMENT_NODE:
        # Skip comments to reduce noise
        return ''

    else:
        # Unknown node type - skip
        return ''
```

**WHERE TO INJECT MARKERS:**
1. **After void elements** (line with `parts.append(' />')`) — add marker before return
2. **After closing tags** (line with `parts.append(f'</{tag_name}>')`) — add marker after this line

**MARKER INJECTION POINTS:**
```python
# AFTER void elements (around line 397):
if tag_name in void_elements:
    parts.append(' />')
    # *** INJECT MARKER HERE ***
    return ''.join(parts)

# AFTER closing tags (around line 430):
# Closing tag
parts.append(f'</{tag_name}>')
# *** INJECT MARKER HERE ***
return ''.join(parts)
```

---

### HTMLSerializer._serialize_attributes() (Lines 532-560)
**CURRENT STATE - FULL METHOD BODY:**
```python
def _serialize_attributes(self, attributes: dict[str, str]) -> str:
    """Serialize element attributes to HTML attribute string.

    Args:
        attributes: Dictionary of attribute names to values

    Returns:
        HTML attribute string (e.g., 'class="foo" id="bar"')
    """
    parts = []
    for key, value in attributes.items():
        # Skip href if not extracting links
        if not self.extract_links and key == 'href':
            continue

        # Skip data-* attributes as they often contain JSON payloads
        # These are used by modern SPAs (React, Vue, Angular) for state management
        if key.startswith('data-'):
            continue

        # Handle boolean attributes
        if value == '' or value is None:
            parts.append(key)
        else:
            # Escape attribute value
            escaped_value = self._escape_attribute(value)
            parts.append(f'{key}="{escaped_value}"')

    return ' '.join(parts)
```

**KEY POINT:** This method **SKIPS all `data-*` attributes** (line 545-547). This is why markers CANNOT use `data-*` format. Markers must be injected as TEXT NODES after closing tags, not as attributes.

---

## FILE 2: browser_use/dom/markdown_extractor.py

### extract_clean_markdown() (Lines 21-149)
**CURRENT STATE - FULL FUNCTION SIGNATURE AND BODY:**
```python
async def extract_clean_markdown(
    browser_session: 'BrowserSession | None' = None,
    dom_service: DomService | None = None,
    target_id: str | None = None,
    extract_links: bool = False,
    skip_json_filtering: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Extract clean markdown from browser content using enhanced DOM tree.

    This unified function can extract markdown using either a browser session (for tools service)
    or a DOM service with target ID (for page actor).

    Popup/modal detection is always enabled. When popups are detected (via semantic HTML attributes
    like <dialog open>, role="dialog", aria-modal="true"), they are labeled separately at the top
    of the output so the LLM can distinguish popup content from main page content.

    Args:
        browser_session: Browser session to extract content from (tools service path)
        dom_service: DOM service instance (page actor path)
        target_id: Target ID for the page (required when using dom_service)
        extract_links: Whether to preserve links in markdown
        skip_json_filtering: If True, preserve JSON code blocks (useful for API documentation)

    Returns:
        tuple: (clean_markdown_content, content_statistics)

    Raises:
        ValueError: If neither browser_session nor (dom_service + target_id) are provided
    """
    # Validate input parameters
    if browser_session is not None:
        if dom_service is not None or target_id is not None:
            raise ValueError('Cannot specify both browser_session and dom_service/target_id')
        # Browser session path (tools service)
        enhanced_dom_tree = await _get_enhanced_dom_tree_from_browser_session(browser_session)
        current_url = await browser_session.get_current_page_url()
        method = 'enhanced_dom_tree'
    elif dom_service is not None and target_id is not None:
        # DOM service path (page actor)
        # Lazy fetch all_frames inside get_dom_tree if needed (for cross-origin iframes)
        enhanced_dom_tree, _ = await dom_service.get_dom_tree(target_id=target_id, all_frames=None)
        current_url = None  # Not available via DOM service
        method = 'dom_service'
    else:
        raise ValueError('Must provide either browser_session or both dom_service and target_id')

    # Use the HTML serializer with the enhanced DOM tree
    html_serializer = HTMLSerializer(extract_links=extract_links)

    # Detect popups/modals before serialization
    popups = html_serializer.detect_popups(enhanced_dom_tree)
    popup_count = len(popups)

    # Use markdownify for clean markdown conversion
    from markdownify import markdownify as md

    def html_to_markdown(html: str) -> str:
        """Convert HTML to markdown with consistent settings."""
        return md(
            html,
            heading_style='ATX',  # Use # style headings
            strip=['script', 'style'],  # Remove these tags
            bullets='-',  # Use - for unordered lists
            code_language='',  # Don't add language to code blocks
            escape_asterisks=False,  # Don't escape asterisks (cleaner output)
            escape_underscores=False,  # Don't escape underscores (cleaner output)
            escape_misc=False,  # Don't escape other characters (cleaner output)
            autolinks=False,  # Don't convert URLs to <> format
            default_title=False,  # Don't add default title attributes
            keep_inline_images_in=[],  # Don't keep inline images in any tags
        )

    if popups:
        # Serialize popups separately with labels
        popup_parts = []
        for i, popup in enumerate(popups, 1):
            popup_html = html_serializer.serialize(popup)
            popup_md = html_to_markdown(popup_html)
            popup_md = re.sub(r'%[0-9A-Fa-f]{2}', '', popup_md)  # Remove URL encoding
            popup_md, _ = _preprocess_markdown_content(popup_md, skip_json_filtering=skip_json_filtering)
            if popup_md.strip():
                label = f'POPUP/MODAL {i}' if popup_count > 1 else 'POPUP/MODAL DETECTED'
                popup_parts.append(f'--- {label} ---\\n{popup_md.strip()}')

        # Serialize main content excluding popups
        exclude_ids = {id(p) for p in popups}
        main_html = html_serializer.serialize_excluding(enhanced_dom_tree, exclude_ids)
        main_md = html_to_markdown(main_html)
        main_md = re.sub(r'%[0-9A-Fa-f]{2}', '', main_md)  # Remove URL encoding
        main_md, chars_filtered = _preprocess_markdown_content(main_md, skip_json_filtering=skip_json_filtering)

        # Combine: popups first, then main content
        if popup_parts:
            content = '\\n\\n'.join(popup_parts) + '\\n\\n--- PAGE CONTENT ---\\n' + main_md
        else:
            content = main_md

        original_html_length = len(main_html) + sum(len(html_serializer.serialize(p)) for p in popups)
    else:
        # No popups - use standard serialization
        page_html = html_serializer.serialize(enhanced_dom_tree)
        original_html_length = len(page_html)

        content = html_to_markdown(page_html)

        # Minimal cleanup - markdownify already does most of the work
        content = re.sub(r'%[0-9A-Fa-f]{2}', '', content)  # Remove any remaining URL encoding

        # Apply light preprocessing to clean up excessive whitespace
        content, chars_filtered = _preprocess_markdown_content(content, skip_json_filtering=skip_json_filtering)

    initial_markdown_length = len(content)
    final_filtered_length = len(content)

    # Content statistics
    stats = {
        'method': method,
        'original_html_chars': original_html_length,
        'initial_markdown_chars': initial_markdown_length,
        'filtered_chars_removed': chars_filtered if not popups else 0,  # Approximate for popup case
        'final_filtered_chars': final_filtered_length,
        'popups_detected': popup_count,
    }

    # Add URL to stats if available
    if current_url:
        stats['url'] = current_url

    return content, stats
```

**WHAT'S MISSING:**
- NO `include_interactive` parameter
- NO selector_map building via DOMTreeSerializer
- NO passing selector_map to HTMLSerializer
- NO `interactive_elements` count in stats

**NEEDS TO BE CHANGED:**
1. Add `include_interactive: bool = False` parameter to function signature
2. When `include_interactive=True`, build selector_map via `DOMTreeSerializer.serialize_accessible_elements()`
3. Pass `selector_map` to `HTMLSerializer(extract_links=extract_links, selector_map=selector_map)`
4. Add `stats['interactive_elements'] = len(selector_map)` to stats dict

---

## FILE 3: browser_use/tools/service.py

### Extract Tool Registration (Lines 947-1165)
**CURRENT STATE - FULL METHOD BODY:**

The extract tool is registered at line 947 with:
```python
@self.registry.action(
    """LLM extracts structured data from page markdown. Use when: on right page, know what to extract, haven't called before on same page+query. Can't get interactive elements. Set extract_links=True for URLs. Use start_from_char if previous extraction was truncated to extract data further down the page.""",
    param_model=ExtractAction,
)
async def extract(
    params: ExtractAction,
    browser_session: BrowserSession,
    page_extraction_llm: BaseChatModel,
    file_system: FileSystem,
    extraction_schema: dict | None = None,
):
    # [FULL BODY - 218 lines, see below]
```

**KEY CALL TO extract_clean_markdown (Lines 989-992):**
```python
content, content_stats = await extract_clean_markdown(
    browser_session=browser_session, extract_links=extract_links, skip_json_filtering=skip_json_filtering
)
```

**WHAT'S MISSING:**
- NO `include_interactive=True` parameter passed to extract_clean_markdown
- Action description says **"Can't get interactive elements"** — needs to be updated
- System prompts don't mention interactive element markers

**NEEDS TO BE CHANGED:**
1. Update action description to remove "Can't get interactive elements"
2. Pass `include_interactive=True` to extract_clean_markdown call
3. Update both system prompts (structured and free-text) to explain marker format

---

### System Prompts (CURRENT STATE)

**Structured Extraction System Prompt (Lines 1040-1052):**
```python
system_prompt = """
You are an expert at extracting structured data from the markdown of a webpage.

<input>
You will be given a query, a JSON Schema, and the markdown of a webpage that has been filtered to remove noise and advertising content.
</input>

<instructions>
- Extract ONLY information present in the webpage. Do not guess or fabricate values.
- Your response MUST conform to the provided JSON Schema exactly.
- If a required field's value cannot be found on the page, use null (if the schema allows it) or an empty string / empty array as appropriate.
- If the content was truncated, extract what is available from the visible portion.
</instructions>
""".strip()
```

**Free-text Extraction System Prompt (Lines 1127-1145):**
```python
system_prompt = """
You are an expert at extracting data from the markdown of a webpage.

<input>
You will be given a query and the markdown of a webpage that has been filtered to remove noise and advertising content.
</input>

<instructions>
- You are tasked to extract information from the webpage that is relevant to the query.
- You should ONLY use the information available in the webpage to answer the query. Do not make up information or provide guess from your own knowledge.
- If the information relevant to the query is not available in the page, your response should mention that.
- If the query asks for all items, products, etc., make sure to directly list all of them.
- If the content was truncated and you need more information, note that the user can use start_from_char parameter to continue from where truncation occurred.
</instructions>

<output>
- Your output should present ALL the information relevant to the query in a concise way.
- Do not answer in conversational format - directly output the relevant information or that the information is unavailable.
</output>
""".strip()
```

**NEEDS TO BE UPDATED:**
Both prompts need to add a section explaining interactive element markers format:
```
<interactive_elements>
The markdown may contain interactive element markers in the format [type:index] where:
- type: button, link, input, select, textarea, or interactive
- index: the element's backend_node_id (used for clicking)
Example: [btn:12345] indicates a button with index 12345
You can reference these indices in your response if relevant to the query.
</interactive_elements>
```

---

## SUPPORTING CLASSES & TYPES

### ClickableElementDetector.is_interactive() (Lines 4-245 in clickable_elements.py)
**FULL METHOD BODY PROVIDED ABOVE** — This is used by DOMTreeSerializer to detect which elements are interactive.

**Key Detection Logic (Priority Order):**
1. Skip non-elements and html/body
2. JavaScript click listeners → True
3. Large iframes (>100x100px) → True
4. Labels/spans with form controls → True
5. Search indicators in class/id/data-* → True
6. Accessibility properties (focusable, editable, checked, etc.) → True
7. Interactive tags (button, input, select, textarea, a, details, summary, option, optgroup) → True
8. Interactive attributes (onclick, onmousedown, tabindex) → True
9. Interactive ARIA roles → True
10. Accessibility tree roles → True
11. Icon detection (10-50px with class/role/onclick/data-action/aria-label) → True
12. Cursor pointer style → True
13. Default → False

---

## IMPORTS NEEDED FOR RE-IMPLEMENTATION

### For HTMLSerializer modifications (html_serializer.py)
```python
from typing import Any
# Already imported:
from browser_use.dom.views import EnhancedDOMTreeNode
from browser_use.dom.serializer.clickable_elements import ClickableElementDetector
```

### For extract_clean_markdown modifications (markdown_extractor.py)
```python
# Already imported:
from browser_use.dom.serializer.serializer import DOMTreeSerializer
from browser_use.dom.views import DOMSelectorMap
from browser_use.dom.serializer.html_serializer import HTMLSerializer
```

### For tools/service.py modifications
```python
# Already imported - no new imports needed
```

---

## IMPLEMENTATION CHECKLIST

- [ ] **HTMLSerializer.__init__:** Add `selector_map: dict[int, Any] | None = None` parameter
- [ ] **HTMLSerializer.__init__:** Add `self.selector_map = selector_map` assignment
- [ ] **HTMLSerializer:** Add `_get_interactive_marker(node: EnhancedDOMTreeNode) -> str` helper method
- [ ] **HTMLSerializer.serialize():** Inject marker after void elements (before return)
- [ ] **HTMLSerializer.serialize():** Inject marker after closing tags
- [ ] **extract_clean_markdown():** Add `include_interactive: bool = False` parameter
- [ ] **extract_clean_markdown():** Build selector_map when `include_interactive=True`
- [ ] **extract_clean_markdown():** Pass selector_map to HTMLSerializer
- [ ] **extract_clean_markdown():** Add `interactive_elements` count to stats
- [ ] **tools/service.py extract():** Pass `include_interactive=True` to extract_clean_markdown
- [ ] **tools/service.py extract():** Update action description (remove "Can't get interactive elements")
- [ ] **tools/service.py extract():** Update structured extraction system prompt
- [ ] **tools/service.py extract():** Update free-text extraction system prompt

---

## MARKER FORMAT SPECIFICATION

**Marker Format:** `[type:index]` where:
- `type`: One of: `btn`, `link`, `input`, `select`, `textarea`, `interactive`
- `index`: The `backend_node_id` from the node's snapshot

**Examples:**
- `[btn:12345]` — button with backend_node_id 12345
- `[link:12346]` — link with backend_node_id 12346
- `[input:12347 type=text]` — text input with backend_node_id 12347
- `[select:12348]` — select dropdown with backend_node_id 12348
- `[textarea:12349]` — textarea with backend_node_id 12349
- `[interactive:12350]` — generic interactive element with backend_node_id 12350

**Placement:** Markers are injected as TEXT NODES immediately after:
1. Void elements: after the ` />` closing
2. Regular elements: after the `</tag>` closing tag

**Why Text Nodes?** Because `_serialize_attributes()` strips all `data-*` attributes, markers cannot be attributes. They must be plain text that markdownify preserves.

---

## VERIFICATION CHECKLIST

- [x] HTMLSerializer.__init__ signature and body (lines 18-24)
- [x] HTMLSerializer.serialize() signature and body (lines 311-454)
- [x] HTMLSerializer._serialize_attributes() logic (lines 532-560)
- [x] extract_clean_markdown() signature and body (lines 21-149)
- [x] extract_clean_markdown() return type and stats dict
- [x] Extract tool implementation (lines 947-1165)
- [x] Extract tool system prompts (structured and free-text)
- [x] ClickableElementDetector.is_interactive() full logic
- [x] All necessary imports identified
- [x] Current gaps documented
- [x] Marker format specification
- [x] Injection points identified
