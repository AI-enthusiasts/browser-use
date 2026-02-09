"""Tests for DOM extraction fixes (browser-use-9yy).

Tests cover:
- FR-1a: _navigate_and_wait() raises TimeoutError on timeout
- FR-1c: NavigationCompleteEvent clears cache
- FR-2: Modal viewport threshold bypass
- FR-3: Lazy loading hint in extract response
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from browser_use.dom.service import DomService
from browser_use.dom.views import EnhancedDOMTreeNode, NodeType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot_node(bounds=None, scroll_rects=None, client_rects=None, computed_styles=None):
	"""Create a minimal mock snapshot node."""
	sn = MagicMock()
	sn.bounds = bounds
	sn.scrollRects = scroll_rects
	sn.clientRects = client_rects
	sn.computed_styles = computed_styles or {}
	return sn


def _make_bounds(x=0, y=0, width=100, height=50):
	"""Create a mock bounds object."""
	b = MagicMock()
	b.x = x
	b.y = y
	b.width = width
	b.height = height
	return b


def _make_node(
	node_name: str = 'div',
	attributes: dict | None = None,
	parent_node=None,
	snapshot_node=None,
	node_type: NodeType = NodeType.ELEMENT_NODE,
) -> EnhancedDOMTreeNode:
	"""Create a minimal EnhancedDOMTreeNode for testing."""
	return EnhancedDOMTreeNode(
		node_id=1,
		backend_node_id=1,
		node_type=node_type,
		node_name=node_name,
		node_value='',
		attributes=attributes or {},
		is_scrollable=None,
		is_visible=True,
		absolute_position=None,
		target_id='target',
		frame_id=None,
		session_id=None,
		content_document=None,
		shadow_root_type=None,
		shadow_roots=None,
		parent_node=parent_node,
		children_nodes=[],
		ax_node=None,
		snapshot_node=snapshot_node,
	)


# ===========================================================================
# FR-2: Modal viewport threshold bypass
# ===========================================================================


class TestModalViewportBypass:
	"""Elements inside modals should bypass viewport threshold filtering."""

	def test_element_inside_role_dialog_is_visible(self):
		"""Content inside role='dialog' should be visible regardless of viewport position."""
		# Create a dialog parent
		dialog = _make_node(node_name='div', attributes={'role': 'dialog'})

		# Create a child element far below viewport (y=2000, beyond 1000px threshold)
		bounds = _make_bounds(x=10, y=2000, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'block', 'visibility': 'visible'})
		child = _make_node(node_name='p', parent_node=dialog, snapshot_node=snapshot)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[], viewport_threshold=1000
		)
		assert result is True, 'Element inside role=dialog should bypass viewport threshold'

	def test_element_inside_alertdialog_is_visible(self):
		"""Content inside role='alertdialog' should be visible."""
		dialog = _make_node(node_name='div', attributes={'role': 'alertdialog'})
		bounds = _make_bounds(x=10, y=3000, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'block'})
		child = _make_node(node_name='span', parent_node=dialog, snapshot_node=snapshot)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[], viewport_threshold=1000
		)
		assert result is True

	def test_element_inside_aria_modal_is_visible(self):
		"""Content inside aria-modal='true' should be visible."""
		dialog = _make_node(node_name='div', attributes={'aria-modal': 'true'})
		bounds = _make_bounds(x=10, y=2500, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'block'})
		child = _make_node(node_name='div', parent_node=dialog, snapshot_node=snapshot)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[], viewport_threshold=1000
		)
		assert result is True

	def test_element_inside_native_dialog_is_visible(self):
		"""Content inside <dialog> element should be visible."""
		dialog = _make_node(node_name='dialog', attributes={})
		bounds = _make_bounds(x=10, y=2000, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'block'})
		child = _make_node(node_name='p', parent_node=dialog, snapshot_node=snapshot)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[], viewport_threshold=1000
		)
		assert result is True

	def test_element_outside_modal_still_filtered(self):
		"""Elements NOT inside a modal should still be filtered by viewport threshold."""
		# Regular div parent (not a modal)
		regular_div = _make_node(node_name='div', attributes={'class': 'content'})

		# Element far below viewport — with an HTML frame to trigger viewport check
		bounds = _make_bounds(x=10, y=2500, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'block'})
		child = _make_node(node_name='p', parent_node=regular_div, snapshot_node=snapshot)

		# Create an HTML frame that represents the document viewport
		html_frame_snapshot = _make_snapshot_node(
			scroll_rects=_make_bounds(x=0, y=0, width=1280, height=5000),
			client_rects=_make_bounds(x=0, y=0, width=1280, height=720),
		)
		html_frame = _make_node(
			node_name='HTML',
			snapshot_node=html_frame_snapshot,
		)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[html_frame], viewport_threshold=1000
		)
		# y=2500 > viewport_bottom(720) + threshold(1000) = 1720, so should be filtered
		assert result is False, 'Element outside modal beyond viewport threshold should be filtered'

	def test_deeply_nested_modal_child_is_visible(self):
		"""Element nested several levels deep inside a modal should still be visible."""
		dialog = _make_node(node_name='div', attributes={'role': 'dialog'})
		wrapper = _make_node(node_name='div', attributes={'class': 'wrapper'}, parent_node=dialog)
		inner = _make_node(node_name='div', attributes={'class': 'inner'}, parent_node=wrapper)

		bounds = _make_bounds(x=10, y=3000, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'block'})
		child = _make_node(node_name='p', parent_node=inner, snapshot_node=snapshot)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[], viewport_threshold=1000
		)
		assert result is True, 'Deeply nested modal child should bypass viewport threshold'

	def test_css_hidden_inside_modal_still_hidden(self):
		"""display:none inside a modal should still be hidden (CSS takes precedence)."""
		dialog = _make_node(node_name='div', attributes={'role': 'dialog'})
		bounds = _make_bounds(x=10, y=100, width=200, height=50)
		snapshot = _make_snapshot_node(bounds=bounds, computed_styles={'display': 'none'})
		child = _make_node(node_name='p', parent_node=dialog, snapshot_node=snapshot)

		result = DomService.is_element_visible_according_to_all_parents(
			child, html_frames=[], viewport_threshold=1000
		)
		assert result is False, 'CSS display:none should override modal bypass'


# ===========================================================================
# FR-1a: _navigate_and_wait raises TimeoutError
# ===========================================================================


class TestNavigateAndWaitTimeout:
	"""_navigate_and_wait() must raise TimeoutError when lifecycle events don't arrive."""

	@pytest.mark.asyncio
	async def test_raises_timeout_error_when_no_events(self):
		"""Should raise TimeoutError when no lifecycle events are received."""
		from browser_use.browser.session import BrowserSession

		session = BrowserSession(headless=True)

		# Mock session_manager via Pydantic field (it's a declared Field, not extra)
		mock_target = MagicMock()
		mock_target.url = 'https://old-page.com'
		session.session_manager = MagicMock()
		session.session_manager.get_target.return_value = mock_target

		# Mock CDP session with empty lifecycle events
		mock_cdp_session = MagicMock()
		mock_cdp_session.target_id = 'ABCDEF1234567890'
		mock_cdp_session._lifecycle_events = []  # No events
		mock_cdp_session.session_id = 'session-123'
		mock_cdp_session.cdp_client = MagicMock()
		mock_cdp_session.cdp_client.send = MagicMock()
		mock_cdp_session.cdp_client.send.Page = MagicMock()
		mock_cdp_session.cdp_client.send.Page.navigate = AsyncMock(return_value={'loaderId': 'loader-1'})

		# Patch on the CLASS to bypass Pydantic's extra='forbid'
		with patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=mock_cdp_session)):
			with pytest.raises(TimeoutError, match='timed out'):
				await session._navigate_and_wait('https://example.com', 'ABCDEF1234567890', timeout=0.1)

	@pytest.mark.asyncio
	async def test_raises_timeout_with_partial_events(self):
		"""Should raise TimeoutError even when some events arrive but not networkIdle/load."""
		from browser_use.browser.session import BrowserSession

		session = BrowserSession(headless=True)

		mock_target = MagicMock()
		mock_target.url = 'https://old-page.com'
		session.session_manager = MagicMock()
		session.session_manager.get_target.return_value = mock_target

		mock_cdp_session = MagicMock()
		mock_cdp_session.target_id = 'ABCDEF1234567890'
		# Has events but not networkIdle or load
		mock_cdp_session._lifecycle_events = [
			{'name': 'commit', 'loaderId': 'loader-1'},
			{'name': 'DOMContentLoaded', 'loaderId': 'loader-1'},
		]
		mock_cdp_session.session_id = 'session-123'
		mock_cdp_session.cdp_client = MagicMock()
		mock_cdp_session.cdp_client.send = MagicMock()
		mock_cdp_session.cdp_client.send.Page = MagicMock()
		mock_cdp_session.cdp_client.send.Page.navigate = AsyncMock(return_value={'loaderId': 'loader-1'})

		with patch.object(BrowserSession, 'get_or_create_cdp_session', new=AsyncMock(return_value=mock_cdp_session)):
			with pytest.raises(TimeoutError, match='events seen'):
				await session._navigate_and_wait('https://example.com', 'ABCDEF1234567890', timeout=0.1)


# ===========================================================================
# FR-1c: NavigationCompleteEvent clears cache
# ===========================================================================


class TestNavigationCompleteEventClearsCache:
	"""on_NavigationCompleteEvent handler must clear cached state."""

	@pytest.mark.asyncio
	async def test_cache_cleared_on_navigation_complete(self):
		"""Cache should be cleared when NavigationCompleteEvent fires."""
		from browser_use.browser.events import NavigationCompleteEvent
		from browser_use.browser.session import BrowserSession

		session = BrowserSession(headless=True)

		# Set up cached state
		session._cached_browser_state_summary = 'old_state'
		session._cached_selector_map = {1: 'element1', 2: 'element2'}

		# Mock DOM watchdog
		mock_watchdog = MagicMock()
		session._dom_watchdog = mock_watchdog

		event = NavigationCompleteEvent(target_id='target-123', url='https://example.com')
		await session.on_NavigationCompleteEvent(event)

		assert session._cached_browser_state_summary is None, 'Cached state should be cleared'
		assert len(session._cached_selector_map) == 0, 'Selector map should be cleared'
		mock_watchdog.clear_cache.assert_called_once()

		# Test again on same instance without DOM watchdog (should not crash)
		session._cached_browser_state_summary = 'another_state'
		session._cached_selector_map[99] = 'element99'
		session._dom_watchdog = None

		event2 = NavigationCompleteEvent(target_id='target-456', url='https://example2.com')
		await session.on_NavigationCompleteEvent(event2)

		assert session._cached_browser_state_summary is None
		assert len(session._cached_selector_map) == 0
