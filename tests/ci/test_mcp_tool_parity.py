"""
Unit tests for MCP tool parity improvements (browser-use-3pi).

Tests cover:
- FR-1: tab_id in browser_get_state
- FR-2: Enhanced scroll with viewport detection
- FR-3: Enhanced click with element description
- FR-4: Enhanced type with actual_value feedback
- FR-5: browser_find_text tool
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import inspect
import asyncio

from browser_use.mcp.server import SessionState


class TestTabIdInGetState:
    """FR-1: browser_get_state should include tab_id in tabs list."""

    @pytest.mark.asyncio
    async def test_get_browser_state_includes_tab_id(self):
        """Verify tabs are serialized with tab_id field via model_dump(by_alias=True)."""
        from browser_use.browser.views import TabInfo

        # Create a TabInfo with a known target_id
        tab = TabInfo(
            target_id='ABCD1234EFGH5678',
            url='https://example.com',
            title='Example'
        )

        # Serialize with by_alias=True (what server.py now does)
        serialized = tab.model_dump(by_alias=True)

        # Should have tab_id (alias) not target_id
        assert 'tab_id' in serialized, "tab_id should be present when using by_alias=True"
        assert serialized['tab_id'] == '5678', "tab_id should be last 4 chars of target_id"
        assert 'target_id' not in serialized, "target_id should not appear with by_alias=True"


class TestEnhancedScroll:
    """FR-2: Enhanced scroll with viewport detection and position feedback."""

    def test_scroll_method_signature(self):
        """Verify _scroll method has new parameters."""
        from browser_use.mcp.server import BrowserUseServer

        sig = inspect.signature(BrowserUseServer._scroll)
        params = list(sig.parameters.keys())

        assert 'direction' in params, "_scroll should have direction param"
        assert 'pages' in params, "_scroll should have pages param"
        assert 'element_index' in params, "_scroll should have element_index param"

    @pytest.mark.asyncio
    async def test_scroll_default_backward_compatible(self):
        """Scroll with no params should default to 500px down (backward compat)."""
        from browser_use.mcp.server import BrowserUseServer
        from browser_use.browser.events import ScrollEvent
        import asyncio

        server = BrowserUseServer()

        # Mock browser_session
        mock_session = MagicMock()
        mock_session.id = 'test-session-id'

        # Mock event bus - capture dispatched event
        dispatched_events = []

        class AwaitableEvent:
            """Mock event that can be awaited."""
            def __await__(self):
                return iter([])

        def capture_dispatch(event):
            dispatched_events.append(event)
            return AwaitableEvent()

        mock_event_bus = MagicMock()
        mock_event_bus.dispatch = capture_dispatch
        mock_session.event_bus = mock_event_bus

        # Mock get_browser_state_summary for position feedback
        mock_state = MagicMock()
        mock_page_info = MagicMock()
        mock_page_info.scroll_y = 500
        mock_page_info.pixels_above = 500
        mock_page_info.pixels_below = 1500
        mock_page_info.page_height = 2500
        mock_page_info.viewport_height = 800
        mock_state.page_info = mock_page_info
        mock_session.get_browser_state_summary = AsyncMock(return_value=mock_state)

        server.browser_session = mock_session

        # Call scroll with default params (no pages specified)
        result = await server._scroll(direction='down')

        # Verify event was dispatched with amount=500 (default)
        assert len(dispatched_events) == 1
        event = dispatched_events[0]
        assert isinstance(event, ScrollEvent)
        assert event.amount == 500, "Default scroll should be 500px"
        assert event.direction == 'down'


class TestEnhancedClick:
    """FR-3: Enhanced click with element description and metadata."""

    def test_build_click_response_exists(self):
        """Verify _build_click_response helper method exists."""
        from browser_use.mcp.server import BrowserUseServer

        assert hasattr(BrowserUseServer, '_build_click_response'), \
            "_build_click_response helper should exist"

    @pytest.mark.asyncio
    async def test_click_response_format(self):
        """Verify _build_click_response returns enriched format."""
        from browser_use.mcp.server import BrowserUseServer

        server = BrowserUseServer()

        # Mock browser_session for tab detection
        mock_browser_session = MagicMock()
        mock_tab = MagicMock()
        mock_tab.target_id = 'TAB12345678'
        mock_browser_session.get_tabs = AsyncMock(return_value=[mock_tab])

        # Create SessionState with mock browser_session
        session_state = SessionState(
            session_id='test-session',
            browser_session=mock_browser_session,
            tools=MagicMock(),
            file_system=MagicMock(),
            session_lock=asyncio.Lock(),
            created_at=0.0,
            last_activity=0.0,
        )

        # Call helper with test data
        tabs_before = {'TAB12345678'}
        click_metadata = {'validation_error': None, 'download': None}

        result = await server._build_click_response(
            element_desc='Button "Submit"',
            index=5,
            click_metadata=click_metadata,
            tabs_before=tabs_before,
            session=session_state,
        )

        # Parse result
        assert 'Clicked' in result
        assert 'Button' in result or 'Submit' in result
        assert '5' in result

    @pytest.mark.asyncio
    async def test_click_detects_new_tab(self):
        """Verify click detects when a new tab opens."""
        from browser_use.mcp.server import BrowserUseServer

        server = BrowserUseServer()

        # Mock browser_session - new tab appeared
        mock_browser_session = MagicMock()
        mock_tab_old = MagicMock()
        mock_tab_old.target_id = 'TAB_OLD_1234'
        mock_tab_new = MagicMock()
        mock_tab_new.target_id = 'TAB_NEW_5678'
        mock_tab_new.url = 'https://newpage.com'
        mock_browser_session.get_tabs = AsyncMock(return_value=[mock_tab_old, mock_tab_new])

        # Create SessionState with mock browser_session
        session_state = SessionState(
            session_id='test-session',
            browser_session=mock_browser_session,
            tools=MagicMock(),
            file_system=MagicMock(),
            session_lock=asyncio.Lock(),
            created_at=0.0,
            last_activity=0.0,
        )

        # tabs_before only had old tab
        tabs_before = {'TAB_OLD_1234'}
        click_metadata = {}

        result = await server._build_click_response(
            element_desc='Link "Open"',
            index=3,
            click_metadata=click_metadata,
            tabs_before=tabs_before,
            session=session_state,
        )

        assert 'new tab' in result.lower()
        assert 'https://newpage.com' in result

    @pytest.mark.asyncio
    async def test_click_returns_download_info(self):
        """Verify click includes download info when present."""
        from browser_use.mcp.server import BrowserUseServer

        server = BrowserUseServer()

        mock_browser_session = MagicMock()
        mock_browser_session.get_tabs = AsyncMock(return_value=[])

        # Create SessionState with mock browser_session
        session_state = SessionState(
            session_id='test-session',
            browser_session=mock_browser_session,
            tools=MagicMock(),
            file_system=MagicMock(),
            session_lock=asyncio.Lock(),
            created_at=0.0,
            last_activity=0.0,
        )

        click_metadata = {
            'download': {'file_name': 'report.pdf', 'file_size': 12345},
            'pdf_generated': True,
            'path': '/path/to/report.pdf'
        }

        result = await server._build_click_response(
            element_desc='Button "Download"',
            index=7,
            click_metadata=click_metadata,
            tabs_before=set(),
            session=session_state,
        )

        assert 'Downloaded' in result or 'report.pdf' in result


class TestEnhancedType:
    """FR-4: Enhanced type with actual_value feedback."""

    def test_type_method_exists(self):
        """Verify _type_text method exists with expected signature."""
        from browser_use.mcp.server import BrowserUseServer

        assert hasattr(BrowserUseServer, '_type_text'), "_type_text should exist"

        sig = inspect.signature(BrowserUseServer._type_text)
        params = list(sig.parameters.keys())
        assert 'index' in params
        assert 'text' in params

    def test_type_code_has_mismatch_warning(self):
        """Verify _type_text code includes actual_value mismatch warning logic."""
        from browser_use.mcp.server import BrowserUseServer
        import inspect

        # Get source code of _type_text
        source = inspect.getsource(BrowserUseServer._type_text)

        # Verify the mismatch warning logic exists
        assert 'actual_value' in source, "_type_text should check actual_value"
        assert 'Warning' in source, "_type_text should include Warning message"
        assert 'event_result' in source, "_type_text should call event_result"


class TestFindText:
    """FR-5: browser_find_text tool."""

    def test_find_text_method_exists(self):
        """Verify _find_text method exists."""
        from browser_use.mcp.server import BrowserUseServer

        assert hasattr(BrowserUseServer, '_find_text'), "_find_text should exist"

        sig = inspect.signature(BrowserUseServer._find_text)
        params = list(sig.parameters.keys())
        assert 'text' in params, "_find_text should have text param"

    @pytest.mark.asyncio
    async def test_find_text_success(self):
        """Verify find_text scrolls to text and returns success."""
        from browser_use.mcp.server import BrowserUseServer

        server = BrowserUseServer()

        # Mock event bus with successful scroll
        mock_browser_session = MagicMock()
        mock_event = MagicMock()
        mock_event_bus = MagicMock()
        mock_event_bus.dispatch = MagicMock(return_value=mock_event)
        mock_browser_session.event_bus = mock_event_bus

        mock_event.__await__ = lambda self: iter([None])
        mock_event.event_result = AsyncMock(return_value={'found': True})

        # Create SessionState with mock browser_session
        session_state = SessionState(
            session_id='test-session',
            browser_session=mock_browser_session,
            tools=MagicMock(),
            file_system=MagicMock(),
            session_lock=asyncio.Lock(),
            created_at=0.0,
            last_activity=0.0,
        )

        result = await server._find_text(text='search term', session=session_state)

        assert 'search term' in result.lower() or 'found' in result.lower() or 'scrolled' in result.lower()

    @pytest.mark.asyncio
    async def test_find_text_not_found(self):
        """Verify find_text returns appropriate message when text not found."""
        from browser_use.mcp.server import BrowserUseServer

        server = BrowserUseServer()

        # Mock browser_session
        mock_browser_session = MagicMock()
        mock_browser_session.id = 'test-session-id'

        # Mock event bus - event_result raises exception for not found
        class FailingEvent:
            async def event_result(self, raise_if_any=False, raise_if_none=False):
                raise Exception("Text not found")

        def dispatch_event(event):
            return FailingEvent()

        mock_event_bus = MagicMock()
        mock_event_bus.dispatch = dispatch_event
        mock_browser_session.event_bus = mock_event_bus

        # Create SessionState with mock browser_session
        session_state = SessionState(
            session_id='test-session',
            browser_session=mock_browser_session,
            tools=MagicMock(),
            file_system=MagicMock(),
            session_lock=asyncio.Lock(),
            created_at=0.0,
            last_activity=0.0,
        )

        result = await server._find_text(text='nonexistent text', session=session_state)

        assert 'not found' in result.lower() or 'not visible' in result.lower()
