# Modal Content Extraction Issue - Root Cause Analysis

## PROBLEM STATEMENT
User reports that `browser_extract_content` doesn't fully extract content from modal/popup windows on Yandex.Eda. When clicking a product card, a modal opens but composition/nutrition info is missing. Scrolling inside the modal + re-extracting helps partially.

## ROOT CAUSE IDENTIFIED: VIEWPORT THRESHOLD FILTERING

### The Issue
**Location:** `browser_use/dom/service.py:240-336` — `is_element_visible_according_to_all_parents()`

The viewport threshold filtering is the culprit:

```python
# Line 242: Default viewport_threshold = 1000px
viewport_threshold: int | None = 1000

# Lines 318-319: Visibility check for elements inside scrollable containers
adjusted_y = current_bounds.y - frame.snapshot_node.scrollRects.y
frame_intersects = (
    adjusted_y < viewport_bottom + viewport_threshold  # ← PROBLEM HERE
    and adjusted_y + current_bounds.height > viewport_top - viewport_threshold
)
```

**What this does:**
- Elements are considered "visible" if they're within 1000px BELOW the current viewport
- Elements scrolled out of view inside a modal are FILTERED OUT if they're beyond this threshold
- When user scrolls inside modal, `scrollRects.y` changes, but the DOM tree is NOT rebuilt
- Re-extraction after scrolling works because the scroll position has changed

### Why This Breaks Modals

**Scenario: Yandex.Eda Product Modal**
1. Modal opens with product info at top (visible)
2. Composition/nutrition info is below the fold (scrolled out of view)
3. `scrollRects.y` = 0 (modal hasn't been scrolled yet)
4. Composition info's `adjusted_y` = 1500px (below viewport)
5. Check: `1500 < 800 + 1000` = `1500 < 1800` = TRUE ✓ (should be included)
6. BUT: If composition is at 2000px, check: `2000 < 1800` = FALSE ✗ (FILTERED OUT)

**The threshold is too small for tall modals!**

### Evidence

**Code Path:**
1. `DomService.get_dom_tree()` calls `is_element_visible_according_to_all_parents()`
2. Elements beyond viewport_threshold are marked `is_visible = False`
3. `DOMTreeSerializer._create_simplified_tree()` skips invisible nodes (line 514)
4. Content never reaches `extract_clean_markdown()`

**Key Code:**
```python
# browser_use/dom/service.py:302-334
if (
    frame.node_type == NodeType.ELEMENT_NODE
    and frame.node_name == 'HTML'
    and frame.snapshot_node
    and frame.snapshot_node.scrollRects
    and frame.snapshot_node.clientRects
):
    # For iframe content, check visibility within iframe's viewport
    viewport_bottom = frame.snapshot_node.clientRects.height
    adjusted_y = current_bounds.y - frame.snapshot_node.scrollRects.y
    
    frame_intersects = (
        adjusted_y < viewport_bottom + viewport_threshold  # ← 1000px threshold
        and adjusted_y + current_bounds.height > viewport_top - viewport_threshold
    )
    
    if not frame_intersects:
        return False  # ← ELEMENT FILTERED OUT
```

## RELATED ISSUES

### 1. Scroll Position NOT Captured in DOM Tree
**Location:** `browser_use/dom/enhanced_snapshot.py:145-155`

The `scrollRects` ARE captured from CDP, but:
- They represent the CURRENT scroll position at extraction time
- If modal content is scrolled out of view, it's filtered out
- Re-extraction after scrolling works because scroll position changed

### 2. No Lazy Loading Detection
**Finding:** No lazy loading detection exists in the codebase
- No IntersectionObserver detection
- No dynamic content loading detection
- Modals with lazy-loaded content will be incomplete

### 3. Bounding Box Filtering (Secondary Issue)
**Location:** `browser_use/dom/serializer/serializer.py:729-858`

The `_apply_bounding_box_filtering()` method:
- Filters elements contained within parent bounds
- Uses `containment_threshold` (default 0.95)
- Can remove content inside scrollable containers if it's "contained" by parent

**However:** This is SECONDARY to viewport threshold issue.

### 4. Scrollable Container Detection Works
**Location:** `browser_use/dom/views.py:622-687` — `is_actually_scrollable` property

Good news: Scrollable containers ARE detected:
```python
# Detects if content is larger than visible area
has_vertical_scroll = scroll_rects.height > client_rects.height + 1
has_horizontal_scroll = scroll_rects.width > client_rects.width + 1
```

But the visibility filtering happens BEFORE this detection is useful.

## SOLUTIONS

### Solution 1: Increase viewport_threshold (Quick Fix)
**Impact:** Low risk, immediate improvement
```python
# browser_use/dom/service.py:53
viewport_threshold: int | None = 5000  # Increase from 1000 to 5000px
```

**Pros:**
- Simple one-line fix
- Captures more content in tall modals
- No architectural changes

**Cons:**
- May include off-screen content from main page
- Doesn't solve lazy loading issue
- Arbitrary threshold still problematic

### Solution 2: Disable viewport_threshold for Modals (Better)
**Impact:** Medium risk, targeted fix
```python
# In is_element_visible_according_to_all_parents()
# Detect if we're inside a modal/dialog
is_inside_modal = any(
    frame.attributes and frame.attributes.get('role') == 'dialog'
    or frame.tag_name == 'dialog'
    for frame in html_frames
)

# Use None (disable threshold) for modal content
effective_threshold = None if is_inside_modal else viewport_threshold
```

**Pros:**
- Targets the specific problem (modals)
- Captures all modal content regardless of scroll position
- Doesn't affect main page extraction

**Cons:**
- Requires modal detection
- May include hidden modal content

### Solution 3: Capture Full Scrollable Content (Best)
**Impact:** High complexity, comprehensive fix
```python
# Before DOM tree construction:
# 1. Detect scrollable containers (modals, divs with overflow:auto)
# 2. For each scrollable container:
#    a. Capture current scroll position
#    b. Scroll to top
#    c. Capture DOM snapshot
#    d. Scroll through entire content
#    e. Merge all snapshots
# 3. Restore original scroll position

# This requires:
# - Modifying DomService.get_dom_tree()
# - Adding scroll capture logic
# - Merging multiple DOM snapshots
```

**Pros:**
- Captures ALL content including lazy-loaded
- Works for any scrollable container
- Most complete solution

**Cons:**
- Complex implementation
- Multiple CDP calls (slower)
- Risk of side effects from scrolling

### Solution 4: Disable Viewport Threshold Entirely (Simplest)
**Impact:** Low risk, but may have side effects
```python
# browser_use/dom/service.py:53
viewport_threshold: int | None = None  # Disable threshold checking
```

**Pros:**
- One-line fix
- Captures all content
- No arbitrary thresholds

**Cons:**
- May include off-screen content from main page
- Could increase extraction size significantly
- Doesn't solve lazy loading

## RECOMMENDED FIX

**Immediate (Quick Win):**
1. Increase `viewport_threshold` from 1000 to 5000px
2. Test on Yandex.Eda modal

**Short-term (Proper Fix):**
1. Detect modals/dialogs in `is_element_visible_according_to_all_parents()`
2. Disable viewport threshold for modal content
3. Add configuration option: `viewport_threshold_for_modals`

**Long-term (Comprehensive):**
1. Implement scroll capture for scrollable containers
2. Add lazy loading detection
3. Merge multiple DOM snapshots

## TESTING STRATEGY

1. **Unit Test:** Modal with content beyond 1000px
   ```python
   # Create modal with 2000px of content
   # Verify all content is extracted
   ```

2. **Integration Test:** Yandex.Eda product modal
   ```python
   # Click product card
   # Extract content
   # Verify composition/nutrition info present
   ```

3. **Regression Test:** Main page extraction
   ```python
   # Ensure off-screen content not included
   # Verify performance not degraded
   ```

## SUMMARY TABLE

| Aspect | Finding | Impact |
|--------|---------|--------|
| Root Cause | Viewport threshold filtering (1000px) | HIGH |
| Location | `service.py:318-319` | - |
| Affected | Modal/popup content beyond 1000px | CRITICAL |
| Scroll Position | Captured but not rebuilt | MEDIUM |
| Lazy Loading | No detection | MEDIUM |
| Bounding Box | Secondary issue | LOW |
| Fix Complexity | Low (1-line) to High (scroll capture) | - |
| Recommended | Increase threshold + modal detection | - |

## CODE REFERENCES

- **Viewport filtering:** `browser_use/dom/service.py:240-336`
- **Scroll position capture:** `browser_use/dom/enhanced_snapshot.py:145-155`
- **Tree simplification:** `browser_use/dom/serializer/serializer.py:435-540`
- **Scrollable detection:** `browser_use/dom/views.py:622-687`
- **Bounding box filtering:** `browser_use/dom/serializer/serializer.py:729-858`
- **Markdown extraction:** `browser_use/dom/markdown_extractor.py:21-164`
