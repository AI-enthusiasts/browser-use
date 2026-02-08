"""Tests for interactive element markers in extract_clean_markdown.

Validates that [btn:N], [link:N], [input:N type=X], [select:N], [textarea:N]
markers appear in markdown output when include_interactive=True.
"""

import asyncio
import os

import pytest
from pytest_httpserver import HTTPServer

from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.dom.markdown_extractor import extract_clean_markdown

# Ensure UTF-8 mode on Windows (PEP 540) — browser-use logs contain emoji
os.environ.setdefault('PYTHONUTF8', '1')


# --- HTML test page ---

INTERACTIVE_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Interactive Elements Test</title></head>
<body>
	<h1>Test Page</h1>
	<p>Some paragraph text.</p>

	<button id="submit-btn">Submit</button>
	<button id="cancel-btn">Cancel</button>

	<a href="/page1">Link One</a>
	<a href="/page2">Link Two</a>

	<form>
		<input type="text" name="username" placeholder="Username" />
		<input type="password" name="password" placeholder="Password" />
		<input type="email" name="email" placeholder="Email" />

		<select name="country">
			<option value="us">United States</option>
			<option value="uk">United Kingdom</option>
		</select>

		<textarea name="bio" placeholder="Tell us about yourself"></textarea>
	</form>
</body>
</html>
"""


# --- Fixtures ---


@pytest.fixture(scope='session')
def http_server():
	"""Test HTTP server serving a page with interactive elements."""
	server = HTTPServer()
	server.start()

	server.expect_request('/interactive').respond_with_data(
		INTERACTIVE_PAGE_HTML,
		content_type='text/html',
	)

	yield server
	server.stop()


@pytest.fixture(scope='session')
def base_url(http_server):
	return f'http://{http_server.host}:{http_server.port}'


@pytest.fixture(scope='module')
async def browser_session():
	"""Create a real headless browser session for testing."""
	session = BrowserSession(
		browser_profile=BrowserProfile(
			headless=True,
			user_data_dir=None,
			keep_alive=True,
		)
	)
	await session.start()
	yield session
	await session.kill()


# --- Helper ---


async def _navigate_and_wait(browser_session, url):
	"""Navigate to URL and wait for page load."""
	from browser_use.tools.service import Tools

	tools = Tools()
	await tools.navigate(url=url, new_tab=False, browser_session=browser_session)
	await asyncio.sleep(0.5)


# --- Tests ---


class TestBrowserStartAndNavigate:
	"""Smoke tests: browser starts, navigates, produces output."""

	async def test_browser_starts(self, browser_session):
		"""Browser session starts and has a CDP connection."""
		assert browser_session._cdp_client_root is not None

	async def test_navigate_to_page(self, browser_session, base_url):
		"""Navigate to test page and verify URL."""
		await _navigate_and_wait(browser_session, f'{base_url}/interactive')
		url = await browser_session.get_current_page_url()
		assert '/interactive' in url

	async def test_extract_markdown_basic(self, browser_session, base_url):
		"""extract_clean_markdown returns non-empty content."""
		await _navigate_and_wait(browser_session, f'{base_url}/interactive')

		content, stats = await extract_clean_markdown(
			browser_session=browser_session,
			include_interactive=False,
		)

		assert len(content) > 0
		assert 'Test Page' in content
		assert stats['method'] == 'enhanced_dom_tree'


class TestInteractiveMarkers:
	"""Verify interactive element markers in markdown output."""

	async def test_markers_present_when_enabled(self, browser_session, base_url):
		"""include_interactive=True produces [btn:], [link:], [input:], etc. markers."""
		await _navigate_and_wait(browser_session, f'{base_url}/interactive')

		content, stats = await extract_clean_markdown(
			browser_session=browser_session,
			include_interactive=True,
		)

		# Print for visual inspection
		print('\n--- MARKDOWN WITH MARKERS ---')
		print(content)
		print('--- END ---')
		print(f'Stats: {stats}')

		# Buttons
		assert '[btn:' in content, f'No button markers found in:\n{content}'

		# Links
		assert '[link:' in content, f'No link markers found in:\n{content}'

		# Inputs
		assert '[input:' in content, f'No input markers found in:\n{content}'

		# Select
		assert '[select:' in content, f'No select markers found in:\n{content}'

		# Textarea
		assert '[textarea:' in content, f'No textarea markers found in:\n{content}'

	async def test_markers_absent_when_disabled(self, browser_session, base_url):
		"""include_interactive=False (default) produces no markers."""
		await _navigate_and_wait(browser_session, f'{base_url}/interactive')

		content, stats = await extract_clean_markdown(
			browser_session=browser_session,
			include_interactive=False,
		)

		assert '[btn:' not in content
		assert '[link:' not in content
		assert '[input:' not in content
		assert '[select:' not in content
		assert '[textarea:' not in content

	async def test_interactive_count_in_stats(self, browser_session, base_url):
		"""Stats include interactive_elements count when markers enabled."""
		await _navigate_and_wait(browser_session, f'{base_url}/interactive')

		content, stats = await extract_clean_markdown(
			browser_session=browser_session,
			include_interactive=True,
		)

		assert 'interactive_elements' in stats
		# Page has: 2 buttons + 2 links + 3 inputs + 1 select + 1 textarea = 9 minimum
		assert stats['interactive_elements'] > 0

	async def test_input_type_in_marker(self, browser_session, base_url):
		"""Input markers include type= attribute."""
		await _navigate_and_wait(browser_session, f'{base_url}/interactive')

		content, stats = await extract_clean_markdown(
			browser_session=browser_session,
			include_interactive=True,
		)

		assert 'type=text' in content or 'type=password' in content or 'type=email' in content
