# BrowserStartEvent Timeout Analysis (30s Timeout Issue)

## INTENT
BrowserStartEvent handler times out after 30s even though Chrome actually starts successfully and CDP is accessible. The event completion signal is lost somewhere in the event chain.

## KEY FINDINGS

### 1. THE 30-SECOND TIMEOUT CONFIGURATION
**Location:** `browser_use/browser/events.py:298`
```python
class BrowserStartEvent(BaseEvent):
    """Start/connect to browser."""
    cdp_url: str | None = None
    launch_options: dict[str, Any] = Field(default_factory=dict)
    event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStartEvent', 30.0))  # seconds
```

**How it works:**
- `event_timeout` is a field on the event itself
- Default is 30 seconds
- Can be overridden via environment variable `TIMEOUT_BrowserStartEvent`
- The timeout is enforced by the `bubus` event bus library (v1.5.6+)

### 2. THE EVENT CHAIN: BrowserStartEvent → on_BrowserStartEvent → BrowserLaunchEvent

**Entry Point:** `browser_use/mcp/server.py:625`
```python
await self.browser_session.start()
```

**Step 1: BrowserSession.start()** (`browser_use/browser/session.py:551-557`)
```python
async def start(self) -> None:
    """Start the browser session."""
    start_event = self.event_bus.dispatch(BrowserStartEvent())
    await start_event  # ← WAITS FOR EVENT COMPLETION (with 30s timeout from bubus)
    # Ensure any exceptions from the event handler are propagated
    await start_event.event_result(raise_if_any=True, raise_if_none=False)
```

**Step 2: on_BrowserStartEvent handler** (`browser_use/browser/session.py:604-689`)
This is the main handler that:
1. Calls `await self.attach_all_watchdogs()` - registers all watchdog handlers
2. If no CDP URL, launches browser:
   - **For local browser:** Dispatches `BrowserLaunchEvent()` and awaits it
   - **For cloud browser:** Calls cloud browser service
3. Connects to CDP via `await self.connect(cdp_url=self.cdp_url)`
4. Dispatches `BrowserConnectedEvent(cdp_url=self.cdp_url)`
5. Returns `{'cdp_url': self.cdp_url}`

**Step 3: BrowserLaunchEvent dispatch** (`browser_use/browser/session.py:638-643`)
```python
launch_event = self.event_bus.dispatch(BrowserLaunchEvent())
await launch_event  # ← WAITS FOR LAUNCH EVENT (with 30s timeout)

# Get the CDP URL from LocalBrowserWatchdog handler result
launch_result: BrowserLaunchResult = cast(
    BrowserLaunchResult, await launch_event.event_result(raise_if_none=True, raise_if_any=True)
)
self.browser_profile.cdp_url = launch_result.cdp_url
```

**Step 4: on_BrowserLaunchEvent handler** (`browser_use/browser/watchdogs/local_browser_watchdog.py:47-60`)
```python
async def on_BrowserLaunchEvent(self, event: BrowserLaunchEvent) -> BrowserLaunchResult:
    """Launch a local browser process."""
    try:
        self.logger.debug('[LocalBrowserWatchdog] Received BrowserLaunchEvent, launching local browser...')
        process, cdp_url = await self._launch_browser()
        self._subprocess = process
        return BrowserLaunchResult(cdp_url=cdp_url)
    except Exception as e:
        self.logger.error(f'[LocalBrowserWatchdog] Exception in on_BrowserLaunchEvent: {e}', exc_info=True)
        raise
```

**Step 5: _launch_browser()** (`browser_use/browser/watchdogs/local_browser_watchdog.py:89-214`)
This method:
1. Finds or installs browser executable
2. Launches browser subprocess with `asyncio.create_subprocess_exec()`
3. **Calls `await self._wait_for_cdp_url(debug_port)`** ← CRITICAL POINT
4. Returns `(process, cdp_url)`

**Step 6: _wait_for_cdp_url()** (`browser_use/browser/watchdogs/local_browser_watchdog.py:370-391`)
```python
@staticmethod
async def _wait_for_cdp_url(port: int, timeout: float = 30) -> str:
    """Wait for the browser to start and return the CDP URL."""
    import aiohttp
    
    start_time = asyncio.get_event_loop().time()
    
    while asyncio.get_event_loop().time() - start_time < timeout:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'http://127.0.0.1:{port}/json/version') as resp:
                    if resp.status == 200:
                        # Chrome is ready
                        return f'http://127.0.0.1:{port}/'
                    else:
                        # Chrome is starting up and returning 502/500 errors
                        await asyncio.sleep(0.1)
        except Exception:
            # Connection error - Chrome might not be ready yet
            await asyncio.sleep(0.1)
    
    raise TimeoutError(f'Browser did not start within {timeout} seconds')
```

### 3. TIMEOUT NESTING PROBLEM

The timeout structure is:
```
BrowserStartEvent (30s timeout from bubus)
  └─ on_BrowserStartEvent handler
      └─ BrowserLaunchEvent (30s timeout from bubus)
          └─ on_BrowserLaunchEvent handler
              └─ _launch_browser()
                  └─ _wait_for_cdp_url(timeout=30)
```

**CRITICAL ISSUE:** There are THREE nested 30-second timeouts:
1. **BrowserStartEvent timeout** (30s) - enforced by bubus
2. **BrowserLaunchEvent timeout** (30s) - enforced by bubus
3. **_wait_for_cdp_url timeout** (30s) - enforced locally

If Chrome takes 25 seconds to start:
- _wait_for_cdp_url completes successfully at 25s
- on_BrowserLaunchEvent returns BrowserLaunchResult at ~25s
- BrowserLaunchEvent completes at ~25s
- on_BrowserStartEvent continues to connect to CDP
- **But BrowserStartEvent timeout is already at 25s used, only 5s left!**
- If CDP connection takes >5s, BrowserStartEvent times out

### 4. THE SIGNAL LOSS POINT

**Most likely culprit:** `browser_use/browser/session.py:651-655`
```python
async with self._connection_lock:
    # Only connect if not already connected
    if self._cdp_client_root is None:
        # Setup browser via CDP (for both local and remote cases)
        await self.connect(cdp_url=self.cdp_url)  # ← THIS CAN HANG
        assert self.cdp_client is not None
        
        # Notify that browser is connected (single place)
        self.event_bus.dispatch(BrowserConnectedEvent(cdp_url=self.cdp_url))
```

**Why the signal gets lost:**
1. `await self.connect(cdp_url=self.cdp_url)` is called AFTER BrowserLaunchEvent completes
2. If this takes >5 seconds (remaining timeout), bubus times out the BrowserStartEvent
3. The handler is still running (connect() is still awaiting), but the event bus has already timed out
4. The event completion signal is never sent back to the caller
5. `await start_event` in `BrowserSession.start()` hangs indefinitely

### 5. HANDLER EXECUTION WRAPPER

**Location:** `browser_use/browser/watchdog_base.py:53-177`

The `attach_handler_to_session()` method wraps handlers with timeout enforcement:
```python
async def unique_handler(event):
    # ... logging ...
    try:
        # **EXECUTE THE EVENT HANDLER FUNCTION**
        result = await actual_handler(event)
        # ... logging ...
        return result
    except Exception as e:
        # ... error handling and recovery ...
        raise
```

The wrapper:
- Logs handler start/completion
- Catches exceptions
- Attempts CDP session recovery on error
- Re-raises original error

**BUT:** The wrapper itself is subject to the bubus timeout. If the handler takes >30s, bubus cancels it.

### 6. CHILD EVENTS THAT MUST COMPLETE

For `on_BrowserStartEvent` to succeed, these must complete:
1. ✅ `attach_all_watchdogs()` - registers handlers
2. ✅ `BrowserLaunchEvent` dispatch and await - launches Chrome
3. ✅ `await launch_event.event_result()` - gets CDP URL
4. ❌ **`await self.connect(cdp_url=self.cdp_url)` - LIKELY HANGS HERE**
5. ✅ `BrowserConnectedEvent` dispatch - notifies listeners
6. ✅ Return `{'cdp_url': self.cdp_url}`

### 7. ENVIRONMENT VARIABLE OVERRIDE

Can override timeout via:
```bash
export TIMEOUT_BrowserStartEvent=60  # Increase to 60 seconds
export TIMEOUT_BrowserLaunchEvent=60  # Increase to 60 seconds
```

## ROOT CAUSE HYPOTHESIS

**The signal gets lost in the bubus event bus timeout mechanism when:**

1. Chrome takes 20-25 seconds to start
2. BrowserLaunchEvent completes successfully
3. on_BrowserStartEvent continues to `await self.connect(cdp_url=...)`
4. CDP connection takes >5 seconds (remaining timeout)
5. bubus times out the BrowserStartEvent handler
6. The handler is cancelled mid-execution
7. The event completion signal is never sent
8. `await start_event` in `BrowserSession.start()` hangs forever

**Why Chrome starts successfully despite timeout:**
- The subprocess is already running
- CDP is already listening
- The timeout only affects the event handler, not the browser process itself

## SOLUTION APPROACHES

### Option 1: Increase Timeout (Quick Fix)
```python
# In on_BrowserStartEvent or BrowserStartEvent definition
event_timeout: float | None = Field(default_factory=lambda: _get_timeout('TIMEOUT_BrowserStartEvent', 90.0))
```

### Option 2: Separate Timeouts (Better)
- BrowserLaunchEvent: 30s (just for launching)
- BrowserStartEvent: 60s (for launch + CDP connection)

### Option 3: Parallel Execution (Best)
- Launch browser in background
- Don't wait for CDP connection in the event handler
- Return CDP URL immediately when available
- Connect to CDP asynchronously

### Option 4: Timeout Awareness (Recommended)
- Track time spent in handler
- Reduce remaining timeout for child operations
- Warn if approaching timeout limit

## FILES TO CHECK

1. `browser_use/browser/session.py` - on_BrowserStartEvent (line 604)
2. `browser_use/browser/events.py` - BrowserStartEvent timeout config (line 298)
3. `browser_use/browser/watchdogs/local_browser_watchdog.py` - _launch_browser (line 89)
4. `browser_use/browser/watchdog_base.py` - handler wrapper (line 53)
5. `browser_use/mcp/server.py` - _init_browser_session (line 583)

## NEXT STEPS

1. Add detailed timing logs to on_BrowserStartEvent
2. Measure time spent in each step
3. Identify which step exceeds the remaining timeout
4. Increase timeout or refactor to avoid nested timeouts
