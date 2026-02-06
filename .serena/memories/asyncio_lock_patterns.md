# asyncio.Lock Usage Patterns in Browser-Use

## Summary
Found 4 distinct uses of asyncio.Lock in the codebase. All follow consistent patterns:
- Initialized in `__init__` or `model_post_init` (Pydantic)
- Used with `async with` context manager
- Protect shared mutable state from concurrent access

## Detailed Patterns

### 1. **SessionManager** (session_manager.py)
**Location:** Lines 48-49
```python
self._lock = asyncio.Lock()
self._recovery_lock = asyncio.Lock()
```

**Purpose:**
- `_lock`: Protects target/session pool mutations (_targets, _sessions, _target_sessions, _session_to_target)
- `_recovery_lock`: Prevents concurrent recovery attempts when agent focus is lost

**Usage Pattern:**
```python
async with self._lock:
    # Modify shared state
    self._targets.clear()
    self._sessions.clear()
    self._target_sessions.clear()
    self._session_to_target.clear()
```

**Key Insight:** Multiple locks for different concerns (pool vs recovery)

---

### 2. **BrowserSession** (session.py)
**Location:** Line 527 (initialized in model_post_init)
```python
def model_post_init(self, __context) -> None:
    self._connection_lock = asyncio.Lock()
```

**Purpose:** Prevents concurrent CDP connections

**Usage Pattern:**
```python
async with self._connection_lock:
    # CDP connection logic
```

**Key Insight:** Initialized in Pydantic's `model_post_init` hook (not __init__)

---

### 3. **StorageStateWatchdog** (storage_state_watchdog.py)
**Location:** Line 47
```python
_save_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
```

**Purpose:** Prevents concurrent file writes to storage_state.json

**Usage Pattern:**
```python
async with self._save_lock:
    # File I/O operations
    storage_state = await self.browser_session._cdp_get_storage_state()
    json_path.write_text(json.dumps(merged_state))
```

**Key Insight:** Uses `default_factory=asyncio.Lock` in PrivateAttr (lazy initialization)

---

### 4. **DemoMode** (demo_mode.py)
**Location:** Line 811
```python
self._lock = asyncio.Lock()
```

**Purpose:** Protects demo panel initialization state

**Usage Pattern:**
```python
async with self._lock:
    script = self._load_script()
    if self._script_identifier is None:
        self._script_identifier = await self.session._cdp_add_init_script(script)
    await self._inject_into_open_pages(script)
    self._panel_ready = True
```

**Key Insight:** Protects both state checks and mutations

---

## Initialization Patterns

### Pattern A: Direct in __init__ (DemoMode, SessionManager)
```python
def __init__(self, ...):
    self._lock = asyncio.Lock()
```

### Pattern B: In model_post_init (BrowserSession - Pydantic)
```python
def model_post_init(self, __context) -> None:
    self._connection_lock = asyncio.Lock()
```

### Pattern C: Lazy with default_factory (StorageStateWatchdog - Pydantic)
```python
_save_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
```

---

## Recommendations for _init_browser_session()

**Best Pattern:** Use Pattern B (model_post_init) if BrowserSession is Pydantic model, else Pattern A

**Rationale:**
1. Consistent with existing BrowserSession pattern
2. Ensures lock is created after model validation
3. Avoids issues with Pydantic field initialization order
4. All async operations use `async with` (never manual acquire/release)

**Code Template:**
```python
# In model_post_init (if Pydantic) or __init__
self._init_browser_session_lock = asyncio.Lock()

# Usage
async with self._init_browser_session_lock:
    # Prevent concurrent _init_browser_session() calls
    if self._browser_session_initialized:
        return
    # ... initialization logic
    self._browser_session_initialized = True
```
