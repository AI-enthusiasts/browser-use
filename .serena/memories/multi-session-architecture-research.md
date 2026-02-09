# Multi-Session Architecture Research — Complete Findings

**Date:** 2026-02-09  
**Status:** RESEARCH COMPLETE — Architecture Proposal Ready

---

## EXECUTIVE SUMMARY

The browser-use MCP server **ALREADY HAS SESSION TRACKING** (`active_sessions` dict) but **LACKS SESSION ISOLATION**. Currently:
- ONE `self.browser_session` (singleton) — all agents share it
- ONE `self.tools` registry (singleton) — all agents share it
- ONE `self.file_system` (singleton) — all agents share it
- `active_sessions` dict tracks sessions but doesn't isolate them

**Problem:** When Agent A navigates to URL X, Agent B's `self.browser_session` also points to URL X. They interfere with each other.

**Solution:** Create a **session-aware architecture** where:
1. Each agent specifies `session_id` in tool calls
2. MCP server maintains `sessions: dict[session_id, SessionState]` where `SessionState` contains isolated `browser_session`, `tools`, `file_system`
3. Tool calls route to the correct session's state
4. Existing tools accept optional `session_id` parameter (backward compatible)

---

## CURRENT ARCHITECTURE

### 1. BrowserUseServer Class Structure

**File:** `browser_use/mcp/server.py` (Lines 188-1288)

#### Instance Variables (Singleton Pattern)
```python
class BrowserUseServer:
    def __init__(self, session_timeout_minutes: int = 10):
        self.server = Server('browser-use')
        self.config = load_browser_use_config()
        self.agent: Agent | None = None
        self.browser_session: BrowserSession | None = None  # ← SINGLETON
        self.tools: Tools | None = None                      # ← SINGLETON
        self.llm: ChatOpenAI = ChatOpenAI(...)               # ← SINGLETON (OK)
        self.file_system: FileSystem | None = None           # ← SINGLETON
        self._telemetry = ProductTelemetry()
        self._start_time = time.time()
        
        # Session management (PARTIAL)
        self.active_sessions: dict[str, dict[str, Any]] = {}  # ← Tracks sessions
        self.session_timeout_minutes = session_timeout_minutes
        self._cleanup_task: Any = None
        
        # Lock for browser session initialization
        self._init_lock = asyncio.Lock()  # ← Protects _init_browser_session
        
        self._setup_handlers()
```

#### What `active_sessions` Currently Tracks
```python
self.active_sessions[session_id] = {
    'session': session,           # BrowserSession object
    'created_at': time.time(),
    'last_activity': time.time(),
    'url': getattr(session, 'current_url', None),
}
```

**Problem:** `active_sessions` stores `BrowserSession` objects but they're NEVER USED. All tool calls use `self.browser_session` instead.

### 2. Tool Dispatch Flow

**File:** `browser_use/mcp/server.py`, Lines 507-580 (`_execute_tool`)

```python
async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
    # Session management tools (don't require active session)
    if tool_name == 'browser_list_sessions':
        return await self._list_sessions()
    elif tool_name == 'browser_close_session':
        return await self._close_session(arguments['session_id'])
    elif tool_name == 'browser_close_all':
        return await self._close_all_sessions()
    
    # Direct browser control tools (require active session)
    elif tool_name.startswith('browser_'):
        # ← ALWAYS initializes THE SAME self.browser_session
        await self._init_browser_session()
        
        if tool_name == 'browser_navigate':
            return await self._navigate(arguments['url'], ...)
        elif tool_name == 'browser_click':
            return await self._click(arguments['index'], ...)
        # ... etc
```

**Problem:** All tools call `await self._init_browser_session()` which initializes/reuses `self.browser_session`. No session isolation.

### 3. Session Initialization

**File:** `browser_use/mcp/server.py`, Lines 582-638 (`_init_browser_session`)

```python
async def _init_browser_session(self, allowed_domains: list[str] | None = None, **kwargs):
    """Initialize browser session using config."""
    async with self._init_lock:
        if self.browser_session and self.tools and self.file_system:
            return  # ← Reuses existing singleton
        
        # ... create BrowserSession, Tools, FileSystem ...
        
        if not self.browser_session:
            profile = BrowserProfile(**profile_data)
            self.browser_session = BrowserSession(browser_profile=profile)
            await self.browser_session.start()
            self._track_session(self.browser_session)  # ← Adds to active_sessions
        
        if not self.tools:
            self.tools = Tools()
        
        if not self.file_system:
            self.file_system = FileSystem(...)
```

**Key Issue:** Once initialized, `self.browser_session` is NEVER CHANGED. All subsequent tool calls use the same instance.

### 4. BrowserSession Class

**File:** `browser_use/browser/session.py` (Lines 93-3551)

#### Instance Variables (Per-Session State)
```python
class BrowserSession(BaseModel):
    id: str  # ← Unique session ID (UUID)
    browser_profile: BrowserProfile
    event_bus: EventBus  # ← Event dispatcher
    agent_focus_target_id: TargetID | None
    _cdp_client_root: CDPClient | None
    _connection_lock: asyncio.Lock
    session_manager: SessionManager  # ← Manages tabs/targets
    _cached_browser_state_summary: BrowserStateSummary | None
    _cached_selector_map: DOMSelectorMap | None
    _downloaded_files: list[str]
    _closed_popup_messages: set[str]
    # ... watchdogs, cloud client, etc.
```

**Key Finding:** Each `BrowserSession` instance is INDEPENDENT. Multiple instances can coexist without global state conflicts.

#### No Global/Class-Level State
- No class variables that would be shared across instances
- Each instance has its own `event_bus`, `session_manager`, `_cdp_client_root`
- No singleton pattern in BrowserSession itself

**Conclusion:** BrowserSession is **SAFE FOR MULTI-INSTANCE USE**.

### 5. Tools Class

**File:** `browser_use/tools/service.py` (not examined in detail, but from imports)

- `Tools()` is a registry of available actions
- Each instance is independent
- Can create multiple `Tools()` instances without conflicts

**Conclusion:** Tools is **SAFE FOR MULTI-INSTANCE USE**.

### 6. FileSystem Class

**File:** `browser_use/filesystem/` (not examined in detail)

- Manages file operations for a specific base directory
- Each instance can have different `base_dir`
- Can create multiple `FileSystem()` instances with different paths

**Conclusion:** FileSystem is **SAFE FOR MULTI-INSTANCE USE**.

---

## SESSION TRACKING ANALYSIS

### Current Session Tracking (Partial)

**File:** `browser_use/mcp/server.py`, Lines 1142-1154

```python
def _track_session(self, session: BrowserSession) -> None:
    """Track a browser session for management."""
    self.active_sessions[session.id] = {
        'session': session,
        'created_at': time.time(),
        'last_activity': time.time(),
        'url': getattr(session, 'current_url', None),
    }

def _update_session_activity(self, session_id: str) -> None:
    """Update the last activity time for a session."""
    if session_id in self.active_sessions:
        self.active_sessions[session_id]['last_activity'] = time.time()
```

**Current Usage:**
- `_track_session()` called in `_init_browser_session()` (Line 598)
- `_update_session_activity()` called in `_navigate()` (Line 779) — but only for `self.browser_session.id`
- Sessions are tracked but NEVER RETRIEVED for tool execution

### Session Listing

**File:** `browser_use/mcp/server.py`, Lines 1156-1181 (`_list_sessions`)

```python
async def _list_sessions(self) -> str:
    """List all active browser sessions."""
    if not self.active_sessions:
        return 'No active browser sessions'
    
    sessions_info = []
    for session_id, session_data in self.active_sessions.items():
        session = session_data['session']
        created_at = time.strftime(...)
        last_activity = time.strftime(...)
        is_active = hasattr(session, 'cdp_client') and session.cdp_client is not None
        
        sessions_info.append({
            'session_id': session_id,
            'created_at': created_at,
            'last_activity': last_activity,
            'active': is_active,
            'current_url': session_data.get('url', 'Unknown'),
            'age_minutes': (time.time() - session_data['created_at']) / 60,
        })
    
    return json.dumps(sessions_info, indent=2)
```

**Key Finding:** `_list_sessions()` correctly lists all tracked sessions. This proves the infrastructure exists.

### Session Closing

**File:** `browser_use/mcp/server.py`, Lines 1183-1208 (`_close_session`)

```python
async def _close_session(self, session_id: str) -> str:
    """Close a specific browser session."""
    if session_id not in self.active_sessions:
        return f'Session {session_id} not found'
    
    session_data = self.active_sessions[session_id]
    session = session_data['session']
    
    try:
        if hasattr(session, 'kill'):
            await session.kill()
        elif hasattr(session, 'close'):
            await session.close()
        
        del self.active_sessions[session_id]
        
        # If this was the current session, clear it
        if self.browser_session and self.browser_session.id == session_id:
            self.browser_session = None
            self.tools = None
        
        return f'Successfully closed session {session_id}'
    except Exception as e:
        return f'Error closing session {session_id}: {str(e)}'
```

**Key Finding:** `_close_session()` correctly closes sessions from `active_sessions`. This proves the infrastructure works.

---

## MCP TOOL REGISTRATION

**File:** `browser_use/mcp/server.py`, Lines 233-505 (`_setup_handlers`)

### Tool Definition Pattern
```python
@self.server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name='browser_navigate',
            description='Navigate to a URL in the browser',
            inputSchema={
                'type': 'object',
                'properties': {
                    'url': {'type': 'string', 'description': 'The URL to navigate to'},
                    'new_tab': {'type': 'boolean', 'description': '...', 'default': False},
                },
                'required': ['url'],
            },
        ),
        # ... more tools ...
    ]
```

### Tool Execution Pattern
```python
@self.server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    """Handle tool execution."""
    try:
        result = await self._execute_tool(name, arguments or {})
        return [types.TextContent(type='text', text=result)]
    except Exception as e:
        logger.error(f'Tool execution failed: {e}', exc_info=True)
        return [types.TextContent(type='text', text=f'Error: {str(e)}')]
```

**Key Finding:** MCP tools are registered with `inputSchema` that defines parameters. Adding `session_id` is straightforward — just add it to the schema.

---

## CONCURRENCY ANALYSIS

### Async/Await Pattern
- MCP server is fully async (`async def _execute_tool`, `async def _navigate`, etc.)
- Can handle concurrent tool calls from multiple agents

### Lock Protection
```python
self._init_lock = asyncio.Lock()  # ← Protects _init_browser_session
```

**Current Issue:** Lock only protects initialization. Once `self.browser_session` is set, concurrent calls can interfere:
- Agent A calls `browser_navigate(url='A')`
- Agent B calls `browser_navigate(url='B')` simultaneously
- Both use `self.browser_session` → race condition

**Solution:** Need per-session locks or session-aware routing.

---

## ARCHITECTURE PROPOSAL

### Phase 1: Session-Aware State Management

**Goal:** Isolate browser state per session

#### New Data Structure
```python
class SessionState:
    """Encapsulates all state for a single browser session."""
    browser_session: BrowserSession
    tools: Tools
    file_system: FileSystem
    created_at: float
    last_activity: float
    session_lock: asyncio.Lock  # ← Per-session lock

class BrowserUseServer:
    def __init__(self, ...):
        # Replace singleton pattern with session dict
        self.sessions: dict[str, SessionState] = {}  # ← NEW
        self.default_session_id: str | None = None   # ← NEW (for backward compat)
        
        # Keep for backward compatibility
        self.browser_session: BrowserSession | None = None
        self.tools: Tools | None = None
        self.file_system: FileSystem | None = None
        
        # ... rest of init ...
```

#### Session Lifecycle Methods
```python
async def _create_session(self, session_id: str | None = None) -> str:
    """Create a new isolated browser session."""
    if session_id is None:
        session_id = str(uuid7str())
    
    if session_id in self.sessions:
        return f'Session {session_id} already exists'
    
    # Initialize session state
    profile = BrowserProfile(**profile_data)
    browser_session = BrowserSession(browser_profile=profile)
    await browser_session.start()
    
    tools = Tools()
    file_system = FileSystem(...)
    
    # Store in sessions dict
    self.sessions[session_id] = SessionState(
        browser_session=browser_session,
        tools=tools,
        file_system=file_system,
        created_at=time.time(),
        last_activity=time.time(),
        session_lock=asyncio.Lock(),
    )
    
    # Set as default if first session
    if self.default_session_id is None:
        self.default_session_id = session_id
        self.browser_session = browser_session
        self.tools = tools
        self.file_system = file_system
    
    return session_id

async def _get_session_state(self, session_id: str | None) -> SessionState:
    """Get session state, using default if not specified."""
    if session_id is None:
        session_id = self.default_session_id
    
    if session_id is None:
        raise ValueError('No session specified and no default session')
    
    if session_id not in self.sessions:
        raise ValueError(f'Session {session_id} not found')
    
    return self.sessions[session_id]
```

### Phase 2: Tool Parameter Updates

**Goal:** Add optional `session_id` to all tools

#### Tool Schema Changes
```python
# Current (backward compatible)
types.Tool(
    name='browser_navigate',
    inputSchema={
        'type': 'object',
        'properties': {
            'url': {'type': 'string', ...},
            'new_tab': {'type': 'boolean', ...},
        },
        'required': ['url'],
    },
)

# Updated (backward compatible)
types.Tool(
    name='browser_navigate',
    inputSchema={
        'type': 'object',
        'properties': {
            'url': {'type': 'string', ...},
            'new_tab': {'type': 'boolean', ...},
            'session_id': {'type': 'string', 'description': 'Session ID (optional, uses default if not specified)'},
        },
        'required': ['url'],
    },
)
```

#### Tool Execution Changes
```python
async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
    # Extract session_id from arguments (optional)
    session_id = arguments.pop('session_id', None)
    
    # ... existing session management tools ...
    
    # Direct browser control tools
    elif tool_name.startswith('browser_'):
        session_state = await self._get_session_state(session_id)
        
        async with session_state.session_lock:
            if tool_name == 'browser_navigate':
                return await self._navigate(
                    session_state,
                    arguments['url'],
                    arguments.get('new_tab', False)
                )
            # ... etc ...
```

### Phase 3: Tool Method Refactoring

**Goal:** Update all tool methods to accept `SessionState` instead of using `self.browser_session`

#### Before
```python
async def _navigate(self, url: str, new_tab: bool = False) -> str:
    if not self.browser_session:
        return 'Error: No browser session active'
    
    self._update_session_activity(self.browser_session.id)
    
    event = self.browser_session.event_bus.dispatch(NavigateToUrlEvent(...))
    await event
    # ...
```

#### After
```python
async def _navigate(self, session_state: SessionState, url: str, new_tab: bool = False) -> str:
    if not session_state.browser_session:
        return 'Error: No browser session active'
    
    self._update_session_activity(session_state.browser_session.id)
    
    event = session_state.browser_session.event_bus.dispatch(NavigateToUrlEvent(...))
    await event
    # ...
```

---

## BACKWARD COMPATIBILITY STRATEGY

### Existing Clients (No `session_id` Parameter)
1. First tool call initializes default session
2. All subsequent calls use default session
3. Behavior identical to current implementation

### New Clients (With `session_id` Parameter)
1. Can create multiple sessions
2. Each tool call specifies which session to use
3. Full isolation between sessions

### Migration Path
1. **Phase 1:** Add session tracking (no breaking changes)
2. **Phase 2:** Add `session_id` parameter to tools (optional, backward compatible)
3. **Phase 3:** Update documentation with multi-session examples
4. **Phase 4:** (Optional) Deprecate singleton pattern in future major version

---

## FILES REQUIRING CHANGES

### Core Changes
1. **`browser_use/mcp/server.py`** (PRIMARY)
   - Add `SessionState` class
   - Add `sessions` dict and `default_session_id`
   - Refactor `_init_browser_session()` → `_create_session()`
   - Update `_execute_tool()` to extract and route `session_id`
   - Update all tool methods (`_navigate`, `_click`, `_type_text`, etc.) to accept `SessionState`
   - Update `_setup_handlers()` to add `session_id` parameter to tool schemas
   - Update session management tools (`_list_sessions`, `_close_session`, etc.)

### Supporting Changes
2. **`browser_use/mcp/__init__.py`** (if needed)
   - Export `SessionState` if used by clients

3. **Tests** (NEW)
   - `tests/ci/mcp/test_multi_session.py` — Test parallel agent execution
   - Verify session isolation (Agent A's navigation doesn't affect Agent B)
   - Verify session creation, listing, closing
   - Verify backward compatibility (no `session_id` parameter)

### Documentation Changes
4. **`docs/` or `README.md`**
   - Document multi-session feature
   - Provide examples of parallel agent usage
   - Explain backward compatibility

---

## IMPLEMENTATION CHECKLIST

### Phase 1: Session State Management
- [ ] Define `SessionState` dataclass
- [ ] Add `sessions` dict to `BrowserUseServer.__init__`
- [ ] Add `default_session_id` to `BrowserUseServer.__init__`
- [ ] Implement `_create_session()` method
- [ ] Implement `_get_session_state()` method
- [ ] Update `_init_browser_session()` to use `_create_session()`

### Phase 2: Tool Parameter Updates
- [ ] Update `_setup_handlers()` to add `session_id` to all tool schemas
- [ ] Update `_execute_tool()` to extract `session_id` from arguments
- [ ] Update `_execute_tool()` to route to correct session

### Phase 3: Tool Method Refactoring
- [ ] Update `_navigate()` signature and implementation
- [ ] Update `_click()` signature and implementation
- [ ] Update `_type_text()` signature and implementation
- [ ] Update `_get_browser_state()` signature and implementation
- [ ] Update `_extract_content()` signature and implementation
- [ ] Update `_scroll()` signature and implementation
- [ ] Update `_go_back()` signature and implementation
- [ ] Update `_send_keys()` signature and implementation
- [ ] Update `_evaluate_js()` signature and implementation
- [ ] Update `_close_browser()` signature and implementation
- [ ] Update `_list_tabs()` signature and implementation
- [ ] Update `_switch_tab()` signature and implementation
- [ ] Update `_close_tab()` signature and implementation
- [ ] Update `_retry_with_browser_use_agent()` signature and implementation

### Phase 4: Session Management Tools
- [ ] Update `_list_sessions()` to use `sessions` dict
- [ ] Update `_close_session()` to use `sessions` dict
- [ ] Update `_close_all_sessions()` to use `sessions` dict
- [ ] Update `_cleanup_expired_sessions()` to use `sessions` dict
- [ ] Add `browser_create_session` tool (optional)
- [ ] Add `browser_set_default_session` tool (optional)

### Phase 5: Testing
- [ ] Write multi-session tests
- [ ] Test backward compatibility (no `session_id`)
- [ ] Test session isolation
- [ ] Test concurrent tool calls
- [ ] Test session cleanup

### Phase 6: Documentation
- [ ] Update README with multi-session examples
- [ ] Document `session_id` parameter
- [ ] Document backward compatibility
- [ ] Add migration guide

---

## RISK ANALYSIS

### Low Risk
- ✅ BrowserSession is safe for multi-instance use (no global state)
- ✅ Tools is safe for multi-instance use
- ✅ FileSystem is safe for multi-instance use
- ✅ Backward compatibility can be maintained

### Medium Risk
- ⚠️ Per-session locks needed to prevent race conditions
- ⚠️ Session cleanup must be robust (no resource leaks)
- ⚠️ Tool methods need careful refactoring (many methods to update)

### Mitigation
- Use `asyncio.Lock` per session (proven pattern in codebase)
- Implement proper cleanup in `_close_session()`
- Systematic refactoring with tests after each method
- Use find_referencing_symbols to ensure all callers updated

---

## SUMMARY

**Current State:**
- Session tracking exists but is unused
- All tools use singleton `self.browser_session`
- Multiple agents interfere with each other

**Proposed Solution:**
- Create `SessionState` class to encapsulate per-session state
- Maintain `sessions: dict[session_id, SessionState]`
- Add optional `session_id` parameter to all tools
- Route tool calls to correct session
- Maintain backward compatibility with default session

**Effort Estimate:**
- Phase 1-2: 4-6 hours (core changes)
- Phase 3: 6-8 hours (refactor 13 tool methods)
- Phase 4: 2-3 hours (session management)
- Phase 5: 3-4 hours (testing)
- Phase 6: 1-2 hours (documentation)
- **Total: 16-23 hours**

**Priority:** HIGH — Blocks parallel agent execution

**Files to Change:** 1 primary (`server.py`), 1 test file (new), 1 doc file (new)
