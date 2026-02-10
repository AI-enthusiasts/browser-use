"""
Tests for multi-session MCP support (browser-use-tnj).

Covers:
- SessionState dataclass
- Session creation (_create_session)
- Session retrieval (_get_session)
- Session isolation (separate browser_session, tools, file_system per session)
- Session lifecycle (_list_sessions, _close_session, _close_all_sessions)
- Tool schema (session_id in inputSchema, optional)
- Backward compatibility (tools work without session_id)
- Session routing in _execute_tool (session_id extraction and dispatch)
"""

import asyncio
import json
import time
from dataclasses import fields as dataclass_fields
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from browser_use.mcp.server import BrowserUseServer, SessionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_state(session_id: str = 'test-session', **overrides) -> SessionState:
    """Create a SessionState with mocked components for testing."""
    defaults = dict(
        session_id=session_id,
        browser_session=MagicMock(),
        tools=MagicMock(),
        file_system=MagicMock(),
        session_lock=asyncio.Lock(),
        created_at=time.time(),
        last_activity=time.time(),
    )
    defaults.update(overrides)
    return SessionState(**defaults)


def _make_server() -> BrowserUseServer:
    """Create a BrowserUseServer without triggering real browser init."""
    return BrowserUseServer()


# ===========================================================================
# 1. SessionState dataclass
# ===========================================================================

class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_session_state_has_required_fields(self):
        """SessionState must expose all per-session state fields."""
        field_names = {f.name for f in dataclass_fields(SessionState)}
        expected = {
            'session_id',
            'browser_session',
            'tools',
            'file_system',
            'session_lock',
            'created_at',
            'last_activity',
        }
        assert expected == field_names, f"Missing or extra fields: {expected.symmetric_difference(field_names)}"

    def test_session_state_instantiation(self):
        """SessionState can be instantiated with all required fields."""
        ss = _make_session_state(session_id='abc')
        assert ss.session_id == 'abc'
        assert ss.browser_session is not None
        assert ss.tools is not None
        assert ss.file_system is not None
        assert isinstance(ss.session_lock, asyncio.Lock)
        assert isinstance(ss.created_at, float)
        assert isinstance(ss.last_activity, float)


# ===========================================================================
# 2. Session creation
# ===========================================================================

class TestSessionCreation:
    """Tests for _create_session."""

    @pytest.mark.asyncio
    async def test_create_session_generates_id(self):
        """_create_session auto-generates a session_id when none provided."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            session_id = await server._create_session()

        assert isinstance(session_id, str)
        assert len(session_id) == 8  # uuid4()[:8]

    @pytest.mark.asyncio
    async def test_create_session_uses_provided_id(self):
        """_create_session uses the caller-supplied session_id."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            session_id = await server._create_session(session_id='my-custom-id')

        assert session_id == 'my-custom-id'

    @pytest.mark.asyncio
    async def test_create_session_adds_to_sessions_dict(self):
        """Created session must appear in server.sessions."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            session_id = await server._create_session(session_id='sess-1')

        assert 'sess-1' in server.sessions
        assert isinstance(server.sessions['sess-1'], SessionState)

    @pytest.mark.asyncio
    async def test_create_session_sets_default_for_first(self):
        """First created session becomes the default."""
        server = _make_server()
        assert server.default_session_id is None

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            session_id = await server._create_session(session_id='first')

        assert server.default_session_id == 'first'

    @pytest.mark.asyncio
    async def test_create_session_does_not_overwrite_default(self):
        """Second session must NOT replace the default."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            await server._create_session(session_id='first')
            await server._create_session(session_id='second')

        assert server.default_session_id == 'first'

    @pytest.mark.asyncio
    async def test_create_session_duplicate_id_raises(self):
        """Creating a session with an existing ID must raise RuntimeError."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            await server._create_session(session_id='dup')

            with pytest.raises(RuntimeError, match='already exists'):
                await server._create_session(session_id='dup')

    @pytest.mark.asyncio
    async def test_create_session_max_sessions_enforced(self):
        """Exceeding max_sessions must raise RuntimeError."""
        server = _make_server()
        server.max_sessions = 2

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            await server._create_session(session_id='s1')
            await server._create_session(session_id='s2')

            with pytest.raises(RuntimeError, match='Maximum sessions limit'):
                await server._create_session(session_id='s3')


# ===========================================================================
# 3. Session retrieval
# ===========================================================================

class TestSessionRetrieval:
    """Tests for _get_session."""

    @pytest.mark.asyncio
    async def test_get_session_none_returns_default(self):
        """_get_session(None) returns the default session."""
        server = _make_server()
        ss = _make_session_state(session_id='default')
        server.sessions['default'] = ss
        server.default_session_id = 'default'

        result = await server._get_session(None)
        assert result is ss

    @pytest.mark.asyncio
    async def test_get_session_by_id(self):
        """_get_session(session_id) returns the correct session."""
        server = _make_server()
        ss_a = _make_session_state(session_id='a')
        ss_b = _make_session_state(session_id='b')
        server.sessions['a'] = ss_a
        server.sessions['b'] = ss_b
        server.default_session_id = 'a'

        result = await server._get_session('b')
        assert result is ss_b

    @pytest.mark.asyncio
    async def test_get_session_invalid_id_raises(self):
        """_get_session with unknown ID must raise RuntimeError."""
        server = _make_server()

        with pytest.raises(RuntimeError, match='not found'):
            await server._get_session('nonexistent')

    @pytest.mark.asyncio
    async def test_get_session_creates_default_when_none_exists(self):
        """_get_session(None) with no default creates a new session (backward compat)."""
        server = _make_server()
        assert server.default_session_id is None
        assert len(server.sessions) == 0

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            result = await server._get_session(None)

        assert isinstance(result, SessionState)
        assert len(server.sessions) == 1
        assert server.default_session_id is not None


# ===========================================================================
# 4. Session isolation
# ===========================================================================

class TestSessionIsolation:
    """Tests for session isolation - each session gets its own objects."""

    @pytest.mark.asyncio
    async def test_sessions_have_different_browser_sessions(self):
        """Two sessions must have distinct browser_session instances."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession') as MockBS:
                # Each call returns a new mock
                MockBS.side_effect = [MagicMock(name='bs1'), MagicMock(name='bs2')]
                await server._create_session(session_id='s1')
                await server._create_session(session_id='s2')

        bs1 = server.sessions['s1'].browser_session
        bs2 = server.sessions['s2'].browser_session
        assert bs1 is not bs2, "Sessions must have different browser_session objects"

    @pytest.mark.asyncio
    async def test_sessions_have_different_tools(self):
        """Two sessions must have distinct Tools instances."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            await server._create_session(session_id='s1')
            await server._create_session(session_id='s2')

        t1 = server.sessions['s1'].tools
        t2 = server.sessions['s2'].tools
        assert t1 is not t2, "Sessions must have different Tools objects"

    @pytest.mark.asyncio
    async def test_sessions_have_different_file_systems(self):
        """Two sessions must have distinct FileSystem instances."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            await server._create_session(session_id='s1')
            await server._create_session(session_id='s2')

        fs1 = server.sessions['s1'].file_system
        fs2 = server.sessions['s2'].file_system
        assert fs1 is not fs2, "Sessions must have different FileSystem objects"

    @pytest.mark.asyncio
    async def test_sessions_have_different_locks(self):
        """Two sessions must have independent locks."""
        server = _make_server()

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession'):
            await server._create_session(session_id='s1')
            await server._create_session(session_id='s2')

        lock1 = server.sessions['s1'].session_lock
        lock2 = server.sessions['s2'].session_lock
        assert lock1 is not lock2, "Sessions must have independent locks"


# ===========================================================================
# 5. Session lifecycle
# ===========================================================================

class TestSessionLifecycle:
    """Tests for session management: list, close, close_all."""

    @pytest.mark.asyncio
    async def test_list_sessions_returns_all(self):
        """_list_sessions returns info for every session."""
        server = _make_server()
        server.sessions['a'] = _make_session_state(session_id='a')
        server.sessions['b'] = _make_session_state(session_id='b')
        server.default_session_id = 'a'

        result = await server._list_sessions()
        parsed = json.loads(result)

        ids = {s['session_id'] for s in parsed}
        assert ids == {'a', 'b'}

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        """_list_sessions with no sessions returns descriptive string."""
        server = _make_server()
        result = await server._list_sessions()
        assert 'No active' in result

    @pytest.mark.asyncio
    async def test_list_sessions_marks_default(self):
        """_list_sessions marks the default session."""
        server = _make_server()
        server.sessions['a'] = _make_session_state(session_id='a')
        server.default_session_id = 'a'

        result = await server._list_sessions()
        parsed = json.loads(result)
        assert parsed[0]['is_default'] is True

    @pytest.mark.asyncio
    async def test_close_session_removes_from_dict(self):
        """_close_session removes the session from server.sessions."""
        server = _make_server()
        mock_bs = MagicMock()
        mock_bs.stop = AsyncMock()
        server.sessions['x'] = _make_session_state(session_id='x', browser_session=mock_bs)
        server.default_session_id = 'x'

        result = await server._close_session('x')

        assert 'x' not in server.sessions
        assert 'Successfully closed' in result

    @pytest.mark.asyncio
    async def test_close_session_reassigns_default(self):
        """Closing the default session reassigns default to another session."""
        server = _make_server()
        mock_bs = MagicMock()
        mock_bs.stop = AsyncMock()
        server.sessions['a'] = _make_session_state(session_id='a', browser_session=mock_bs)
        server.sessions['b'] = _make_session_state(session_id='b')
        server.default_session_id = 'a'

        await server._close_session('a')

        # Default should be reassigned to remaining session
        assert server.default_session_id == 'b'

    @pytest.mark.asyncio
    async def test_close_session_not_found(self):
        """_close_session with unknown ID returns error message (no exception)."""
        server = _make_server()
        result = await server._close_session('ghost')
        assert 'not found' in result

    @pytest.mark.asyncio
    async def test_close_all_sessions_clears_dict(self):
        """_close_all_sessions removes all sessions and resets default."""
        server = _make_server()
        for sid in ('a', 'b', 'c'):
            mock_bs = MagicMock()
            mock_bs.stop = AsyncMock()
            server.sessions[sid] = _make_session_state(session_id=sid, browser_session=mock_bs)
        server.default_session_id = 'a'

        result = await server._close_all_sessions()

        assert len(server.sessions) == 0
        assert server.default_session_id is None
        assert 'Closed 3' in result

    @pytest.mark.asyncio
    async def test_close_all_sessions_empty(self):
        """_close_all_sessions with no sessions returns descriptive string."""
        server = _make_server()
        result = await server._close_all_sessions()
        assert 'No active' in result


# ===========================================================================
# 6. Tool schema
# ===========================================================================


async def _get_registered_tools(server: BrowserUseServer):
    """Invoke the registered list_tools handler to get tool definitions."""
    import mcp.types as types

    handler = server.server.request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method='tools/list'))
    return result.root.tools


class TestToolSchema:
    """Tests for tool schema - session_id in inputSchema."""

    @pytest.mark.asyncio
    async def test_browser_tools_have_session_id_in_schema(self):
        """All browser_ tools (except session management) must have session_id property."""
        server = _make_server()
        tools = await _get_registered_tools(server)

        # Tools that should have session_id
        session_mgmt = {
            'browser_create_session', 'browser_list_sessions',
            'browser_close_session', 'browser_close_all',
        }
        browser_tools_with_session = [
            t for t in tools
            if t.name.startswith('browser_') and t.name not in session_mgmt
        ]

        assert len(browser_tools_with_session) > 0, "Should have browser_ tools to test"

        for tool in browser_tools_with_session:
            props = tool.inputSchema.get('properties', {})
            assert 'session_id' in props, (
                f"Tool '{tool.name}' missing session_id in schema properties"
            )

    @pytest.mark.asyncio
    async def test_session_id_is_optional_in_schema(self):
        """session_id must NOT be in 'required' for any browser_ tool."""
        server = _make_server()
        tools = await _get_registered_tools(server)

        session_mgmt = {
            'browser_create_session', 'browser_list_sessions',
            'browser_close_session', 'browser_close_all',
        }
        browser_tools_with_session = [
            t for t in tools
            if t.name.startswith('browser_') and t.name not in session_mgmt
        ]

        for tool in browser_tools_with_session:
            required = tool.inputSchema.get('required', [])
            assert 'session_id' not in required, (
                f"Tool '{tool.name}' has session_id in required - must be optional"
            )

    @pytest.mark.asyncio
    async def test_session_management_tools_exist(self):
        """Session management tools must be registered."""
        server = _make_server()
        tools = await _get_registered_tools(server)
        tool_names = {t.name for t in tools}

        assert 'browser_create_session' in tool_names
        assert 'browser_list_sessions' in tool_names
        assert 'browser_close_session' in tool_names
        assert 'browser_close_all' in tool_names


# ===========================================================================
# 7. Backward compatibility
# ===========================================================================

class TestBackwardCompatibility:
    """Tests for backward compatibility - tools work without session_id."""

    @pytest.mark.asyncio
    async def test_execute_tool_without_session_id_uses_default(self):
        """Calling a browser_ tool without session_id uses the default session."""
        server = _make_server()

        # Pre-populate a default session
        mock_bs = MagicMock()
        mock_bs.event_bus = MagicMock()
        mock_bs.get_current_page_url = AsyncMock(return_value='https://example.com')

        class AwaitableEvent:
            def __await__(self):
                return iter([])
            async def event_result(self, **kwargs):
                return None

        mock_bs.event_bus.dispatch = MagicMock(return_value=AwaitableEvent())

        ss = _make_session_state(session_id='default', browser_session=mock_bs)
        server.sessions['default'] = ss
        server.default_session_id = 'default'

        # Call navigate WITHOUT session_id
        result = await server._execute_tool('browser_navigate', {'url': 'https://example.com'})

        assert 'example.com' in result

    @pytest.mark.asyncio
    async def test_execute_tool_auto_creates_default_session(self):
        """First tool call without session_id auto-creates a default session."""
        server = _make_server()
        assert len(server.sessions) == 0

        # Mock _create_session to avoid real browser launch
        mock_bs = MagicMock()
        mock_bs.event_bus = MagicMock()
        mock_bs.get_current_page_url = AsyncMock(return_value='https://example.com')

        class AwaitableEvent:
            def __await__(self):
                return iter([])
            async def event_result(self, **kwargs):
                return None

        mock_bs.event_bus.dispatch = MagicMock(return_value=AwaitableEvent())

        with patch.object(server, 'config', {}), \
             patch('browser_use.mcp.server.get_default_profile', return_value={}), \
             patch('browser_use.mcp.server.BrowserSession', return_value=mock_bs):
            result = await server._execute_tool('browser_navigate', {'url': 'https://example.com'})

        # A default session should have been created
        assert len(server.sessions) == 1
        assert server.default_session_id is not None


# ===========================================================================
# 8. Session routing in _execute_tool
# ===========================================================================

class TestSessionRouting:
    """Tests for session routing in _execute_tool."""

    @pytest.mark.asyncio
    async def test_session_id_extracted_from_arguments(self):
        """_execute_tool extracts session_id from arguments and routes correctly."""
        server = _make_server()

        # Create two sessions with mock browser_sessions
        mock_bs_a = MagicMock(name='bs_a')
        mock_bs_a.event_bus = MagicMock()
        mock_bs_a.get_current_page_url = AsyncMock(return_value='https://a.com')

        class AwaitableEvent:
            def __await__(self):
                return iter([])
            async def event_result(self, **kwargs):
                return None

        mock_bs_a.event_bus.dispatch = MagicMock(return_value=AwaitableEvent())

        ss_a = _make_session_state(session_id='sess-a', browser_session=mock_bs_a)
        server.sessions['sess-a'] = ss_a
        server.default_session_id = 'sess-a'

        mock_bs_b = MagicMock(name='bs_b')
        mock_bs_b.event_bus = MagicMock()
        mock_bs_b.get_current_page_url = AsyncMock(return_value='https://b.com')
        mock_bs_b.event_bus.dispatch = MagicMock(return_value=AwaitableEvent())

        ss_b = _make_session_state(session_id='sess-b', browser_session=mock_bs_b)
        server.sessions['sess-b'] = ss_b

        # Call with explicit session_id targeting session B
        result = await server._execute_tool(
            'browser_navigate',
            {'url': 'https://b.com', 'session_id': 'sess-b'},
        )

        # Session B's browser_session should have been used
        mock_bs_b.event_bus.dispatch.assert_called_once()
        mock_bs_a.event_bus.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_id_not_passed_to_tool_method(self):
        """session_id must be popped from arguments - not forwarded to tool methods."""
        server = _make_server()

        mock_bs = MagicMock()
        mock_bs.event_bus = MagicMock()
        mock_bs.get_current_page_url = AsyncMock(return_value='https://example.com')

        class AwaitableEvent:
            def __await__(self):
                return iter([])
            async def event_result(self, **kwargs):
                return None

        mock_bs.event_bus.dispatch = MagicMock(return_value=AwaitableEvent())

        ss = _make_session_state(session_id='s1', browser_session=mock_bs)
        server.sessions['s1'] = ss
        server.default_session_id = 's1'

        # Arguments include session_id - it should be popped before dispatch
        args = {'url': 'https://example.com', 'session_id': 's1'}
        await server._execute_tool('browser_navigate', args)

        # session_id should have been consumed (popped) from args
        assert 'session_id' not in args

    @pytest.mark.asyncio
    async def test_session_management_tools_bypass_routing(self):
        """Session management tools (list, close, create) don't go through session routing."""
        server = _make_server()

        # browser_list_sessions should work with no sessions
        result = await server._execute_tool('browser_list_sessions', {})
        assert 'No active' in result

    @pytest.mark.asyncio
    async def test_execute_tool_inner_dispatches_to_correct_methods(self):
        """_execute_tool_inner routes tool names to the correct session-aware methods."""
        server = _make_server()
        ss = _make_session_state(session_id='test')

        # Patch individual tool methods to verify dispatch
        with patch.object(server, '_navigate', new_callable=AsyncMock, return_value='nav ok') as mock_nav:
            result = await server._execute_tool_inner(
                'browser_navigate', {'url': 'https://x.com'}, ss
            )
            mock_nav.assert_called_once_with('https://x.com', False, session=ss)
            assert result == 'nav ok'

        with patch.object(server, '_go_back', new_callable=AsyncMock, return_value='back ok') as mock_back:
            result = await server._execute_tool_inner('browser_go_back', {}, ss)
            mock_back.assert_called_once_with(session=ss)
            assert result == 'back ok'

    @pytest.mark.asyncio
    async def test_execute_tool_unknown_returns_error(self):
        """Unknown tool name returns error string."""
        server = _make_server()
        result = await server._execute_tool('totally_unknown_tool', {})
        assert 'Unknown tool' in result

    @pytest.mark.asyncio
    async def test_session_lock_acquired_during_tool_execution(self):
        """Tool execution acquires the session lock for concurrency safety."""
        server = _make_server()

        mock_bs = MagicMock()
        mock_bs.event_bus = MagicMock()
        mock_bs.get_current_page_url = AsyncMock(return_value='https://example.com')

        class AwaitableEvent:
            def __await__(self):
                return iter([])
            async def event_result(self, **kwargs):
                return None

        mock_bs.event_bus.dispatch = MagicMock(return_value=AwaitableEvent())

        session_lock = asyncio.Lock()
        ss = _make_session_state(session_id='locked', browser_session=mock_bs, session_lock=session_lock)
        server.sessions['locked'] = ss
        server.default_session_id = 'locked'

        # Acquire the lock externally - tool execution should block
        async with session_lock:
            # Start tool execution in background - it should be blocked by the lock
            task = asyncio.create_task(
                server._execute_tool('browser_navigate', {'url': 'https://example.com'})
            )
            # Give the event loop a chance to schedule the task
            await asyncio.sleep(0.05)
            # Task should NOT be done because lock is held
            assert not task.done(), "Tool execution should be blocked by session lock"

        # After releasing lock, task should complete
        result = await asyncio.wait_for(task, timeout=2.0)
        assert 'example.com' in result
