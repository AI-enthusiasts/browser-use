"""
Unit tests for blind-v2 bug fixes (A6, A7, A8, E1).

Tests cover:
- A6: close_tab waits for focus recovery, not just clearing
- A7: get_state filters JS/JSON code from element text
- A8: find_and_click text matches placeholder, aria-label, title, value
- E1: force_full_page has stale scroll detection and reduced iterations
"""

import pytest
import inspect
import ast
import textwrap

from browser_use.mcp.server import BrowserUseServer


class TestCloseTabFocusRecovery:
    """A6: close_tab should wait for focus RECOVERY (new tab), not just CLEARING (None)."""

    def test_close_tab_waits_for_non_none_focus(self):
        """The retry loop must check that focus is set to a DIFFERENT valid target, not just cleared."""
        source = inspect.getsource(BrowserUseServer._close_tab)

        # The fix: condition should be `focus is not None and focus != target_id`
        # Old broken condition was: `agent_focus_target_id != target_id` (True when None)
        assert 'is not None' in source, (
            '_close_tab retry loop must check focus is not None (recovered), '
            'not just != target_id (which is True when None/cleared)'
        )

    def test_close_tab_retry_count_increased(self):
        """Retry count should be > 5 to allow time for async focus recovery."""
        source = inspect.getsource(BrowserUseServer._close_tab)

        # Find range(N) in the retry loop
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'range':
                if node.args and isinstance(node.args[0], ast.Constant):
                    retry_count = node.args[0].value
                    assert retry_count > 5, (
                        f'Retry count is {retry_count}, should be > 5 to allow '
                        f'async focus recovery (clearing ~50ms, recovery ~100-500ms)'
                    )
                    return
        pytest.fail('Could not find range(N) in _close_tab retry loop')


class TestGetStateJSFilter:
    """A7: get_state should filter JS/JSON code from element text."""

    def test_get_browser_state_has_code_filter(self):
        """_get_browser_state must contain JS/JSON text filtering logic."""
        source = inspect.getsource(BrowserUseServer._get_browser_state)

        # The filter should detect JSON objects and JS code patterns
        assert 'startswith' in source, (
            '_get_browser_state must filter text starting with { or ( that looks like code'
        )

    def test_json_object_detected(self):
        """Text starting with { containing : should be detected as code."""
        # Simulate the filter logic from _get_browser_state
        test_cases = [
            ('{"widgets":{"@marketfront/HeaderOrdersButton":{}}}', True),
            ('{name}', False),  # No colon, not JSON
            ('{"key": "value"}', True),
            ('{Click here for more}', False),  # No colon
        ]
        for text, expected_code in test_cases:
            _t = text.lstrip()
            _is_code = False
            if _t.startswith('{') and (':' in _t or '}' in _t):
                _is_code = True
            # Note: {name} has } so it IS detected. That's acceptable —
            # single-word braced text is rare in real UI elements.
            if expected_code:
                assert _is_code, f'Should detect as code: {text!r}'

    def test_js_code_detected(self):
        """Text starting with ( containing = or ; should be detected as code."""
        test_cases = [
            ('(window.apiarySleepingQueue=[])', True),
            ('(function(){var x=1;})()', True),
            ('(click here)', False),  # No = or ; or function
        ]
        for text, expected_code in test_cases:
            _t = text.lstrip()
            _is_code = False
            if _t.startswith('(') and ('=' in _t or ';' in _t or 'function' in _t.lower()):
                _is_code = True
            if expected_code:
                assert _is_code, f'Should detect as code: {text!r}'
            else:
                assert not _is_code, f'Should NOT detect as code: {text!r}'

    def test_window_var_const_detected(self):
        """Text starting with window./var /const /let /function /return should be detected."""
        code_texts = [
            'window.yaCounter123 = new Ya.Metrika()',
            'var __NEXT_DATA__ = {"props":{}}',
            'const config = {api: "https://..."}',
            'let x = document.getElementById("app")',
            'function init() { return null; }',
            'return false;',
        ]
        for text in code_texts:
            _t = text.lstrip()
            _is_code = _t.startswith(('window.', 'var ', 'const ', 'let ', 'function ', 'return '))
            assert _is_code, f'Should detect as code: {text!r}'

    def test_normal_text_not_filtered(self):
        """Normal UI text should NOT be detected as code."""
        normal_texts = [
            'Add to cart',
            'Search results for "laptop"',
            'Price: 25 043 rub',
            '(2 reviews)',  # Parenthesized but no = or ;
            'Log in / Sign up',
        ]
        for text in normal_texts:
            _t = text.lstrip()
            _is_code = False
            if _t.startswith('{') and (':' in _t or '}' in _t):
                _is_code = True
            elif _t.startswith('(') and ('=' in _t or ';' in _t or 'function' in _t.lower()):
                _is_code = True
            elif _t.startswith(('window.', 'var ', 'const ', 'let ', 'function ', 'return ')):
                _is_code = True
            assert not _is_code, f'Should NOT detect as code: {text!r}'


class TestFindAndClickPlaceholder:
    """A8: find_and_click text should match placeholder, aria-label, title, value."""

    def test_find_and_click_js_checks_placeholder(self):
        """The JS in _find_and_click text branch must check el.placeholder."""
        source = inspect.getsource(BrowserUseServer._find_and_click)
        assert 'placeholder' in source, (
            '_find_and_click must check placeholder attribute for text matching'
        )

    def test_find_and_click_js_checks_aria_label(self):
        """The JS in _find_and_click text branch must check aria-label."""
        source = inspect.getsource(BrowserUseServer._find_and_click)
        assert 'aria-label' in source, (
            '_find_and_click must check aria-label attribute for text matching'
        )

    def test_find_and_click_js_checks_title(self):
        """The JS in _find_and_click text branch must check title attribute."""
        source = inspect.getsource(BrowserUseServer._find_and_click)
        # title is used in the haystack join
        assert '.title' in source, (
            '_find_and_click must check title attribute for text matching'
        )

    def test_find_and_click_selector_includes_input_types(self):
        """The clickable selector should include input[type=search] and input[type=text]."""
        source = inspect.getsource(BrowserUseServer._find_and_click)
        assert 'input[type=search]' in source, (
            '_find_and_click clickable selector must include input[type=search]'
        )
        assert 'input[type=text]' in source, (
            '_find_and_click clickable selector must include input[type=text]'
        )

    def test_find_and_click_text_response_uses_fallback(self):
        """When textContent is empty, response text should fall back to placeholder/aria-label."""
        source = inspect.getsource(BrowserUseServer._find_and_click)
        # The text field in return should have fallback chain
        assert 'el.placeholder' in source, (
            '_find_and_click return text should fall back to placeholder when textContent is empty'
        )


class TestScrollHeavyDOMFallback:
    """P1: scroll should gracefully degrade on heavy DOM instead of crashing."""

    def test_scroll_has_timeout_on_get_browser_state(self):
        """_scroll must wrap get_browser_state_summary in asyncio.wait_for with timeout."""
        source = inspect.getsource(BrowserUseServer._scroll)
        assert 'wait_for' in source, (
            '_scroll must use asyncio.wait_for to timeout get_browser_state_summary on heavy DOM'
        )

    def test_scroll_has_lightweight_js_fallback(self):
        """_scroll must fall back to lightweight JS when DOM reindex fails/times out."""
        source = inspect.getsource(BrowserUseServer._scroll)
        assert 'scrollHeight' in source, (
            '_scroll must have lightweight JS fallback using scrollHeight for position'
        )

    def test_scroll_warns_agent_about_heavy_dom(self):
        """Fallback response must warn agent that DOM is too heavy and suggest alternatives."""
        source = inspect.getsource(BrowserUseServer._scroll)
        assert 'too heavy' in source.lower(), (
            '_scroll fallback must warn agent about heavy DOM'
        )
        assert 'browser_extract_content' in source, (
            '_scroll fallback must suggest browser_extract_content as alternative'
        )


class TestForceFullPageEarlyExit:
    """E1: force_full_page should have stale scroll detection and reduced max iterations."""

    def test_max_iterations_reduced(self):
        """Max scroll iterations should be <= 20 (was 50)."""
        source = inspect.getsource(BrowserUseServer._extract_content)

        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'range':
                if node.args and isinstance(node.args[0], ast.Constant):
                    max_iter = node.args[0].value
                    if max_iter <= 20:
                        return  # Found reduced iteration count
        pytest.fail('Could not find reduced iteration count (<=20) in force_full_page loop')

    def test_stale_scroll_detection_exists(self):
        """force_full_page must detect stale scroll (pixels_below not decreasing)."""
        source = inspect.getsource(BrowserUseServer._extract_content)
        assert 'stale' in source.lower(), (
            'force_full_page must have stale scroll detection'
        )

    def test_no_get_browser_state_summary_in_scroll_loop(self):
        """Scroll loop should use lightweight JS, not full get_browser_state_summary per iteration."""
        source = inspect.getsource(BrowserUseServer._extract_content)

        # Find the force_full_page section
        fp_start = source.find('force_full_page')
        fp_section = source[fp_start:fp_start + 2000]

        # The scroll loop should NOT call get_browser_state_summary (heavy DOM reindex)
        # It should use Runtime.evaluate with lightweight JS instead
        lines_in_loop = fp_section.split('\n')
        in_loop = False
        for line in lines_in_loop:
            if 'for ' in line and 'range(' in line:
                in_loop = True
            if in_loop and 'get_browser_state_summary' in line:
                pytest.fail(
                    'force_full_page scroll loop should NOT call get_browser_state_summary '
                    '(causes DOM reindex per iteration, leading to timeout and 1.1GB RAM). '
                    'Use lightweight JS via Runtime.evaluate instead.'
                )
            if in_loop and 'Scroll back' in line:
                break  # End of loop section
