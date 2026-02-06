# Modal & Popup Handling in browser_extract_content

## INTENT SUMMARY
Understanding how `browser_extract_content` processes popups and modal windows — whether they're included in extracted content or filtered out.

---

## KEY FINDINGS

### 1. VISIBILITY DETERMINATION (is_visible)

**Location:** `browser_use/dom/service.py:240-336` — `is_element_visible_according_to_all_parents()`

**How is_visible is calculated:**
```python
# CSS-based visibility checks (FIRST PASS)
display = computed_styles.get('display', '').lower()
visibility = computed_styles.get('visibility', '').lower()
opacity = computed_styles.get('opacity', '1')

if display == 'none' or visibility == 'hidden':
    return False  # ❌ FILTERED OUT

if float(opacity) <= 0:
    return False  # ❌ FILTERED OUT

# Bounds check (SECOND PASS)
current_bounds = node.snapshot_node.bounds
if not current_bounds:
    return False  # ❌ FILTERED OUT (no layout)

# Viewport intersection check (THIRD PASS)
# For each parent frame (iframe/document):
#   - Check if element intersects with viewport
#   - Apply scroll offset
#   - Use viewport_threshold (default 1000px below viewport)
```

**CRITICAL:** `is_visible` is set DURING DOM tree construction (line 874), not during extraction.

---

### 2. OPEN MODALS — INCLUDED OR EXCLUDED?

**Answer: DEPENDS ON CSS**

**INCLUDED (YES):**
- ✅ Modal with `display: block` (or any non-none value)
- ✅ Modal with `visibility: visible` (default)
- ✅ Modal with `opacity: > 0`
- ✅ Modal with `position: fixed` or `position: absolute`
- ✅ Modal with high `z-index` (z-index is NOT checked in visibility logic)

**EXCLUDED (NO):**
- ❌ Modal with `display: none`
- ❌ Modal with `visibility: hidden`
- ❌ Modal with `opacity: 0` or `opacity: <= 0`
- ❌ Modal outside viewport AND beyond `viewport_threshold` (default 1000px)

**Example:**
```html
<!-- INCLUDED in extraction -->
<div class="modal" style="display: block; position: fixed; z-index: 9999;">
  <h2>Modal Title</h2>
  <p>Modal content</p>
</div>

<!-- EXCLUDED from extraction -->
<div class="modal" style="display: none;">
  <h2>Hidden Modal</h2>
</div>
```

---

### 3. CLOSED MODALS (display:none) — INCLUDED OR EXCLUDED?

**Answer: EXCLUDED (NO)**

**Evidence:**
- `is_element_visible_according_to_all_parents()` checks: `if display == 'none': return False`
- Closed modals are filtered out at the DOM tree construction phase
- They never reach the extraction stage

**Code path:**
1. DOM tree built → `is_visible = False` for `display:none` elements
2. Serializer checks `is_visible` → skips invisible elements
3. Markdown extraction only processes visible elements

---

### 4. Z-INDEX ORDERING PROBLEM

**Answer: YES, POTENTIAL ISSUE**

**The Problem:**
- `z-index` is NOT checked in visibility logic
- Paint order filtering (paint_order.py) removes elements COVERED by higher z-index elements
- BUT: Paint order filtering only removes elements if they're COMPLETELY COVERED by a higher z-index element

**Paint Order Logic (paint_order.py:150-200):**
```python
# Elements are marked as ignored_by_paint_order if:
if rect_union.contains(rect):  # Element is completely covered by higher z-index elements
    node.ignored_by_paint_order = True
```

**Scenario: Modal under main content**
```html
<div class="main-content" style="z-index: 1;">
  <p>Main content</p>
</div>
<div class="modal" style="z-index: 0; position: fixed;">
  <!-- Modal is UNDER main content -->
</div>
```

**Result:**
- If modal is completely covered by main content → EXCLUDED (paint order filtering)
- If modal is partially visible → INCLUDED (paint order only removes fully covered elements)
- **Risk:** Modal content may appear in markdown BEFORE main content (DOM order, not visual order)

---

### 5. SPECIAL HANDLING FOR DIALOGS/MODALS

**Answer: NO SPECIAL HANDLING**

**Evidence:**
- No checks for `<dialog>` tag
- No checks for `role="dialog"`
- No checks for `aria-modal="true"`
- No checks for `aria-hidden="true"` (only `hidden` attribute)
- Modals treated like any other element

**Implication:** Modals are extracted based purely on CSS visibility, not semantic HTML.

---

### 6. OVERLAY ELEMENTS (backdrop, overlay classes)

**Answer: INCLUDED IF VISIBLE**

**Backdrop handling:**
- `::backdrop` pseudo-element is NOT in DOM tree (CSS-only)
- Overlay divs with `position: fixed; z-index: 999` are included if visible
- Overlay divs with `opacity: 0` are excluded (opacity check)
- Overlay divs with `pointer-events: none` are still included (not checked)

**Example:**
```html
<!-- INCLUDED -->
<div class="overlay" style="position: fixed; z-index: 999; opacity: 0.5;">
  <!-- Overlay content -->
</div>

<!-- EXCLUDED -->
<div class="overlay" style="position: fixed; z-index: 999; opacity: 0;">
  <!-- Invisible overlay -->
</div>
```

---

## EXTRACTION PIPELINE

### Phase 1: DOM Tree Construction (service.py)
1. Build DOM tree from CDP
2. For each node: `is_visible = is_element_visible_according_to_all_parents()`
3. Nodes with `is_visible = False` are marked but still included in tree

### Phase 2: Serialization (serializer.py)
1. `_create_simplified_tree()` — filters out invisible nodes
2. `_optimize_tree()` — removes unnecessary parents
3. `PaintOrderRemover.calculate_paint_order()` — removes elements covered by higher z-index
4. `_apply_bounding_box_filtering()` — removes elements outside viewport

### Phase 3: HTML Serialization (html_serializer.py)
1. Converts simplified tree to HTML
2. Skips `display:none` code tags (JSON data)
3. Skips base64 images
4. Skips data-* attributes

### Phase 4: Markdown Extraction (markdown_extractor.py)
1. Uses markdownify to convert HTML to markdown
2. Removes script/style tags
3. Applies light preprocessing

---

## VISIBILITY CHECKS SUMMARY

| Check | Location | Filters |
|-------|----------|---------|
| CSS display | `is_element_visible_according_to_all_parents()` | `display:none` ❌ |
| CSS visibility | `is_element_visible_according_to_all_parents()` | `visibility:hidden` ❌ |
| CSS opacity | `is_element_visible_according_to_all_parents()` | `opacity:0` ❌ |
| Bounds | `is_element_visible_according_to_all_parents()` | No bounds ❌ |
| Viewport intersection | `is_element_visible_according_to_all_parents()` | Outside viewport+1000px ❌ |
| Paint order | `PaintOrderRemover.calculate_paint_order()` | Fully covered by higher z-index ❌ |
| Z-index | NONE | Not checked ⚠️ |
| aria-hidden | NONE | Not checked ⚠️ |
| role="dialog" | NONE | Not checked ⚠️ |

---

## RECOMMENDATIONS FOR IMPROVEMENT

### 1. Add aria-hidden Support
```python
# In is_element_visible_according_to_all_parents()
if node.attributes and node.attributes.get('aria-hidden', '').lower() == 'true':
    return False
```

### 2. Add Dialog/Modal Detection
```python
# In serializer.py _create_simplified_tree()
is_modal = (
    node.tag_name and node.tag_name.lower() == 'dialog'
    or node.attributes and node.attributes.get('role') == 'dialog'
    or node.attributes and node.attributes.get('aria-modal') == 'true'
)
# Could apply special handling (e.g., always include if visible)
```

### 3. Improve Z-Index Ordering in Markdown
```python
# In serializer.py, track z-index during tree construction
# Sort elements by z-index before serialization
# Ensures modal content appears after background content in markdown
```

### 4. Add Inert Attribute Support
```python
# In is_element_visible_according_to_all_parents()
if node.attributes and 'inert' in node.attributes:
    return False  # inert elements are not interactive
```

### 5. Better Overlay Detection
```python
# Detect overlay patterns:
# - position: fixed + z-index > 1000
# - opacity < 0.1 (likely transparent overlay)
# - pointer-events: none (non-interactive overlay)
# Could mark as "overlay" for special handling
```

---

## EDGE CASES

### Case 1: Modal with position:absolute
- ✅ INCLUDED if visible and within viewport
- ⚠️ May be hidden by scrolling (not fixed position)

### Case 2: Modal in iframe
- ✅ INCLUDED if iframe is visible
- ✅ Visibility checked relative to iframe viewport
- ⚠️ Cross-origin iframes may not have snapshot data

### Case 3: Modal with transform:translateX(-9999px)
- ✅ INCLUDED (transform not checked in visibility)
- ⚠️ Will appear in markdown even though visually hidden

### Case 4: Modal with clip-path
- ✅ INCLUDED (clip-path not checked)
- ⚠️ May include clipped content

### Case 5: Modal with backdrop-filter
- ✅ INCLUDED (backdrop-filter not checked)
- ⚠️ Overlay effect not visible in markdown

---

## SUMMARY TABLE

| Question | Answer | Evidence |
|----------|--------|----------|
| Open modals included? | YES (if CSS visible) | `is_visible` check in service.py |
| Closed modals included? | NO | `display:none` filtered |
| Z-index ordering issue? | YES | Paint order only removes fully covered |
| Dialog tag special handling? | NO | No checks for `<dialog>` |
| aria-modal support? | NO | Not checked |
| Overlay elements included? | YES (if visible) | Treated like any element |

---

## RELATED CODE FILES

- `browser_use/dom/service.py:240-336` — Visibility calculation
- `browser_use/dom/serializer/serializer.py:490-540` — Tree simplification
- `browser_use/dom/serializer/paint_order.py:150-200` — Paint order filtering
- `browser_use/dom/serializer/html_serializer.py:70-85` — HTML serialization
- `browser_use/dom/markdown_extractor.py:20-80` — Markdown extraction
