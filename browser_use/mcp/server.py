"""MCP Server for browser-use - exposes browser automation capabilities via Model Context Protocol.

This server provides tools for:
- Running autonomous browser tasks with an AI agent
- Direct browser control (navigation, clicking, typing, etc.)
- Content extraction from web pages
- File system operations

Usage:
    uvx browser-use --mcp

Or as an MCP server in Claude Desktop or other MCP clients:
    {
        "mcpServers": {
            "browser-use": {
                "command": "uvx",
                "args": ["browser-use[cli]", "--mcp"],
                "env": {
                    "OPENAI_API_KEY": "sk-proj-1234567890",
                }
            }
        }
    }
"""

import os
import sys

from browser_use.llm import ChatAWSBedrock

# Set environment variables BEFORE any browser_use imports to prevent early logging
os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'critical'
os.environ['BROWSER_USE_SETUP_LOGGING'] = 'false'

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Configure logging for MCP mode - redirect to stderr but preserve critical diagnostics
logging.basicConfig(
	stream=sys.stderr, level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True
)

try:
	import psutil

	PSUTIL_AVAILABLE = True
except ImportError:
	PSUTIL_AVAILABLE = False

# Add browser-use to path if running from source
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import and configure logging to use stderr before other imports
from browser_use.logging_config import setup_logging


def _configure_mcp_server_logging():
	"""Configure logging for MCP server mode - redirect all logs to stderr to prevent JSON RPC interference."""
	# Set environment to suppress browser-use logging during server mode
	os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'warning'
	os.environ['BROWSER_USE_SETUP_LOGGING'] = 'false'  # Prevent automatic logging setup

	# Configure logging to stderr for MCP mode - preserve warnings and above for troubleshooting
	setup_logging(stream=sys.stderr, log_level='warning', force_setup=True)

	# Also configure the root logger and all existing loggers to use stderr
	logging.root.handlers = []
	stderr_handler = logging.StreamHandler(sys.stderr)
	stderr_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
	logging.root.addHandler(stderr_handler)
	logging.root.setLevel(logging.CRITICAL)

	# Configure all existing loggers to use stderr and CRITICAL level
	for name in list(logging.root.manager.loggerDict.keys()):
		logger_obj = logging.getLogger(name)
		logger_obj.handlers = []
		logger_obj.setLevel(logging.CRITICAL)
		logger_obj.addHandler(stderr_handler)
		logger_obj.propagate = False


# Configure MCP server logging before any browser_use imports to capture early log lines
_configure_mcp_server_logging()

# Additional suppression - disable all logging completely for MCP mode
logging.disable(logging.CRITICAL)

# Import browser_use modules
from browser_use import ActionModel, PatternLearningAgent
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.config import get_default_llm, get_default_profile, load_browser_use_config
from browser_use.filesystem.file_system import FileSystem
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.tools.service import Tools

logger = logging.getLogger(__name__)


def _ensure_all_loggers_use_stderr():
	"""Ensure ALL loggers only output to stderr, not stdout."""
	# Get the stderr handler
	stderr_handler = None
	for handler in logging.root.handlers:
		if hasattr(handler, 'stream') and handler.stream == sys.stderr:  # type: ignore
			stderr_handler = handler
			break

	if not stderr_handler:
		stderr_handler = logging.StreamHandler(sys.stderr)
		stderr_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

	# Configure root logger
	logging.root.handlers = [stderr_handler]
	logging.root.setLevel(logging.CRITICAL)

	# Configure all existing loggers
	for name in list(logging.root.manager.loggerDict.keys()):
		logger_obj = logging.getLogger(name)
		logger_obj.handlers = [stderr_handler]
		logger_obj.setLevel(logging.CRITICAL)
		logger_obj.propagate = False


# Ensure stderr logging after all imports
_ensure_all_loggers_use_stderr()


# Try to import MCP SDK
try:
	import mcp.server.stdio
	import mcp.types as types
	from mcp.server import NotificationOptions, Server
	from mcp.server.models import InitializationOptions

	MCP_AVAILABLE = True

	# Configure MCP SDK logging to stderr as well
except ImportError:
	MCP_AVAILABLE = False
	logger.error('MCP SDK not installed. Install with: pip install mcp')
	sys.exit(1)

from browser_use.telemetry import MCPServerTelemetryEvent, ProductTelemetry
from browser_use.utils import create_task_with_error_handling, get_browser_use_version


def get_parent_process_cmdline() -> str | None:
	"""Get the command line of all parent processes up the chain."""
	if not PSUTIL_AVAILABLE:
		return None

	try:
		cmdlines = []
		current_process = psutil.Process()
		parent = current_process.parent()

		while parent:
			try:
				cmdline = parent.cmdline()
				if cmdline:
					cmdlines.append(' '.join(cmdline))
			except (psutil.AccessDenied, psutil.NoSuchProcess):
				# Skip processes we can't access (like system processes)
				pass

			try:
				parent = parent.parent()
			except (psutil.AccessDenied, psutil.NoSuchProcess):
				# Can't go further up the chain
				break

		return ';'.join(cmdlines) if cmdlines else None
	except Exception:
		# If we can't get parent process info, just return None
		return None



@dataclass
class SessionState:
	"""Encapsulates per-session state for multi-session MCP server."""
	session_id: str
	browser_session: BrowserSession
	tools: Tools
	file_system: FileSystem
	session_lock: asyncio.Lock
	created_at: float
	last_activity: float


class BrowserUseServer:
	"""MCP Server for browser-use capabilities."""

	def __init__(self, session_timeout_minutes: int = 10):
		# Ensure all logging goes to stderr (in case new loggers were created)
		_ensure_all_loggers_use_stderr()

		self.server = Server('browser-use')
		self.config = load_browser_use_config()
		self.browser_session: BrowserSession | None = None
		self.tools: Tools | None = None
		# LLM for page content extraction (browser_extract_content)
		llm_config = get_default_llm(self.config)
		base_url = llm_config.get('base_url') or os.getenv('OPENAI_PROXY_BASE_URL') or 'http://localhost:8080/v1'
		provider = llm_config.get('provider') or os.getenv('BROWSER_USE_LLM_PROVIDER') or ''
		if provider:
			proxy_base_url = f'{base_url.rstrip("/")}/{provider}'
		else:
			proxy_base_url = base_url
		# Model for extraction: config > env > default (small/fast model recommended)
		extraction_model = (
			llm_config.get('extraction_model') or os.getenv('BROWSER_USE_EXTRACTION_MODEL') or llm_config.get('model')
		)
		self.llm: ChatOpenAI = ChatOpenAI(
			model=extraction_model,
			api_key=llm_config.get('api_key') or os.getenv('OPENAI_API_KEY') or 'not-needed',
			base_url=proxy_base_url,
			temperature=0.0,
		)
		self.file_system: FileSystem | None = None
		self._telemetry = ProductTelemetry()
		self._start_time = time.time()

		# Multi-session management
		self.sessions: dict[str, SessionState] = {}  # session_id -> SessionState
		self.default_session_id: str | None = None
		self.max_sessions: int = 10
		self._sessions_lock = asyncio.Lock()  # global lock for sessions dict mutation

		self.session_timeout_minutes = session_timeout_minutes
		self._cleanup_task: Any = None

		# Lock for browser session initialization (prevents race conditions)
		self._init_lock = asyncio.Lock()

		# Setup handlers
		self._setup_handlers()

	def _setup_handlers(self):
		"""Setup MCP server handlers."""

		@self.server.list_tools()
		async def handle_list_tools() -> list[types.Tool]:
			"""List all available browser-use tools."""
			return [
				# Agent tools
				# Direct browser control tools
				types.Tool(
					name='browser_navigate',
					description='Open a URL in the browser with verified loading - reports actual URL after redirects and detects failed SPA navigations. For search, pass query params directly in the URL (?query=term) instead of clicking + typing + Enter.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'url': {'type': 'string', 'description': 'The URL to navigate to'},
							'new_tab': {'type': 'boolean', 'description': 'Whether to open in a new tab', 'default': False},
						},
						'required': ['url'],
					},
				),
				types.Tool(
					name='browser_click',
					description='Click an interactive element by index and wait for completion. Indices come from browser_extract_content or browser_get_state. Critical: always get a fresh index immediately before clicking - SPA pages re-render the DOM and stale indices will miss the target.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'index': {
								'type': 'integer',
								'description': 'The index of the link or element to click (from browser_get_state)',
							},
							'new_tab': {
								'type': 'boolean',
								'description': 'Whether to open any resulting navigation in a new tab',
								'default': False,
							},
						},
						'required': ['index'],
					},
				),
				types.Tool(
					name='browser_type',
					description='Enter text into an input field by index. Appends to existing content - to replace, send Ctrl+A via browser_send_keys first. Handles non-ASCII input (Cyrillic, CJK, emoji).',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'index': {
								'type': 'integer',
								'description': 'The index of the input element (from browser_get_state)',
							},
							'text': {'type': 'string', 'description': 'The text to type'},
						},
						'required': ['index', 'text'],
					},
				),
				types.Tool(
					name='browser_get_state',
					description='Full DOM snapshot - page URL, title, tab list, and every interactive element with index and parent context. Output is large (hundreds of elements); prefer browser_extract_content for targeted data extraction. Best for debugging when extract misses elements or you need raw DOM structure.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'include_screenshot': {
								'type': 'boolean',
								'description': 'Whether to include a screenshot of the current page',
								'default': False,
							}
						},
					},
				),
				types.Tool(
					name='browser_extract_content',
					description='Structured page content extraction via natural-language query - returns text, element indices for clicking, and auto-detected popups/modals. Call after every navigation to catch popups before they block interaction. Supports pagination for long pages and schema-validated output for reliable parsing.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'query': {'type': 'string', 'description': 'What information to extract from the page'},
							'extract_links': {
								'type': 'boolean',
								'description': 'Whether to include links in the extraction',
								'default': False,
							},
							'skip_json_filtering': {
								'type': 'boolean',
								'description': 'Set True to preserve JSON code blocks (useful for API documentation). By default, JSON blobs are filtered as noise.',
								'default': False,
							},
							'start_from_char': {
								'type': 'integer',
								'description': 'Start extraction from this character offset (for paginating long content)',
								'default': 0,
							},
							'output_schema': {
								'type': 'object',
								'description': 'Optional JSON Schema dict. When provided, extraction returns validated JSON matching this schema instead of free-text.',
							},
						},
						'required': ['query'],
					},
				),
				types.Tool(
					name='browser_scroll',
					description='Vertical page scroll that triggers lazy-loading of dynamic content. After scrolling, call browser_extract_content to read newly loaded elements - they are invisible until extracted. Returns scroll position.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'direction': {
								'type': 'string',
								'enum': ['up', 'down', 'left', 'right'],
								'description': 'Scroll direction',
								'default': 'down',
							},
							'pages': {
								'type': 'number',
								'description': 'Pages to scroll (0.1-10.0). Overrides default 500px. 1.0 = one viewport height.',
							},
							'element_index': {
								'type': 'integer',
								'description': 'Element index to scroll within (for modals, dropdowns, scrollable containers)',
							},
						},
					},
				),
				types.Tool(
					name='browser_go_back',
					description='Browser history back navigation. On SPA sites may not work as expected - the app may handle routing internally without updating browser history. Verify with browser_get_state after going back.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
						},
					},
				),
				types.Tool(
					name='browser_find_text',
					description='Find text on the page and scroll to it. Returns success/failure message.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'text': {
								'type': 'string',
								'description': 'Text to find and scroll to on the page',
							},
						},
						'required': ['text'],
					},
				),
				types.Tool(
					name='browser_send_keys',
					description='Keyboard input - individual keys and shortcuts (Enter, Escape, Tab, Ctrl+A, ArrowDown). Essential before browser_type: send Ctrl+A to select existing text, then type replaces it. Keys are case-insensitive. Supports modifier combos (ctrl+shift+k).',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'keys': {
								'type': 'string',
								'description': 'Key name (Enter, Escape, Tab, ArrowDown, PageDown, Backspace, Delete, Home, End, F1-F12, Space) or shortcut (ctrl+a, ctrl+c, shift+Tab). One key/shortcut per call.',
							}
						},
						'required': ['keys'],
					},
				),
				types.Tool(
					name='browser_evaluate',
					description='JavaScript execution in page context with return value. Reaches what other tools cannot - shadow DOM, computed styles, localStorage, custom API calls, or text not exposed as interactive elements. Script runs synchronously; for async operations, wrap in a Promise.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'expression': {
								'type': 'string',
								'description': 'JavaScript expression to evaluate. Can be a simple expression (document.title) or an IIFE ((() => { ... })()). The return value is serialized to string.',
							}
						},
						'required': ['expression'],
					},
				),
				# Tab management
				types.Tool(
					name='browser_list_tabs',
					description='Tab inventory - returns tab_id, URL, and title for every open tab. Required before browser_switch_tab or browser_close_tab since tab_id is not available from other tools.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
						},
					}
				),
				types.Tool(
					name='browser_switch_tab',
					description='Tab focus switch by 4-char tab_id from browser_list_tabs. After switching, call browser_extract_content to read the new tab content - it is not returned automatically.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'tab_id': {'type': 'string', 'description': '4 Character Tab ID of the tab to switch to'}},
						'required': ['tab_id'],
					},
				),
				types.Tool(
					name='browser_close_tab',
					description='Close a specific tab by 4-char tab_id from browser_list_tabs. If closing the active tab, the browser switches to another open tab automatically.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID. If not provided, uses default session.',
							},
							'tab_id': {'type': 'string', 'description': '4 Character Tab ID of the tab to close'}},
						'required': ['tab_id'],
					},
				),
				types.Tool(
					name='retry_with_browser_use_agent',
					description='Autonomous browser agent that executes multi-step tasks with pattern learning - remembers UI patterns (cookie banners, login forms, search flows) across sessions. Inherits current browser state (page, cookies, cart). Best for complex workflows where manual step-by-step control is impractical or repeatedly failing.',
					inputSchema={
						'type': 'object',
						'properties': {
							'task': {
								'type': 'string',
								'description': 'The high-level goal and detailed step-by-step description of the task the AI browser agent needs to attempt, along with any relevant data needed to complete the task and info about previous attempts.',
							},
							'max_steps': {
								'type': 'integer',
								'description': 'Maximum number of steps an agent can take.',
								'default': 100,
							},
							'model': {
								'type': 'string',
								'description': 'LLM model to use (e.g., gemini-3-pro-preview, gpt-4o, claude-sonnet-4-20250514)',
								'default': 'gemini-3-pro-preview',
							},
							'allowed_domains': {
								'type': 'array',
								'items': {'type': 'string'},
								'description': 'List of domains the agent is allowed to visit (security feature)',
								'default': [],
							},
							'use_vision': {
								'type': 'boolean',
								'description': 'Whether to use vision capabilities (screenshots) for the agent',
								'default': True,
							},
						},
						'required': ['task'],
					},
				),
				# Browser session management tools
				types.Tool(
					name='browser_create_session',
					description='Create a new isolated browser session. Each session has its own browser instance, tabs, cookies, and storage. Returns session_id for use in other browser_ tools.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Optional custom session ID. Auto-generated if omitted.',
							},
							'headless': {
								'type': 'boolean',
								'description': 'Run browser without visible window. Defaults to profile setting.',
							},
							'viewport': {
								'type': 'object',
								'properties': {
									'width': {'type': 'integer'},
									'height': {'type': 'integer'},
								},
								'description': 'Browser viewport dimensions.',
							},
						},
					},
				),
				types.Tool(
					name='browser_list_sessions',
					description='Active browser session inventory - each session is a separate browser instance (not a tab). Returns session IDs and last activity timestamps for session management.',
					inputSchema={'type': 'object', 'properties': {}},
				),
				types.Tool(
					name='browser_close_session',
					description='Close browser sessions by ID. Without arguments, returns a list of all active sessions and their IDs. Pass session_id to close a specific session.',
					inputSchema={
						'type': 'object',
						'properties': {
							'session_id': {
								'type': 'string',
								'description': 'Session ID to close. Omit to see active sessions.',
							}
						},
					},
				),
			]

		@self.server.list_resources()
		async def handle_list_resources() -> list[types.Resource]:
			"""List available resources (none for browser-use)."""
			return []

		@self.server.list_prompts()
		async def handle_list_prompts() -> list[types.Prompt]:
			"""List available prompts (none for browser-use)."""
			return []

		@self.server.call_tool()
		async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
			"""Handle tool execution."""
			start_time = time.time()
			error_msg = None
			try:
				result = await self._execute_tool(name, arguments or {})
				return [types.TextContent(type='text', text=result)]
			except Exception as e:
				error_msg = str(e)
				logger.error(f'Tool execution failed: {e}', exc_info=True)
				return [types.TextContent(type='text', text=f'Error: {str(e)}')]
			finally:
				# Capture telemetry for tool calls
				duration = time.time() - start_time
				self._telemetry.capture(
					MCPServerTelemetryEvent(
						version=get_browser_use_version(),
						action='tool_call',
						tool_name=name,
						duration_seconds=duration,
						error_message=error_msg,
					)
				)

	async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
		"""Execute a browser-use tool with session routing."""

		# Agent-based tools (no session routing)
		if tool_name == 'retry_with_browser_use_agent':
			return await self._retry_with_browser_use_agent(
				task=arguments['task'],
				max_steps=arguments.get('max_steps', 100),
				model=arguments.get('model', 'gemini-3-pro-preview'),
				allowed_domains=arguments.get('allowed_domains', []),
				use_vision=arguments.get('use_vision', True),
			)

		# Session management tools (don't require active session)
		if tool_name == 'browser_create_session':
			session_id = await self._create_session(
				session_id=arguments.get('session_id'),
				headless=arguments.get('headless'),
				viewport=arguments.get('viewport'),
			)
			return f'Created session: {session_id}'

		elif tool_name == 'browser_list_sessions':
			return await self._list_sessions()

		elif tool_name == 'browser_close_session':
			return await self._close_session(arguments.get('session_id'))

		# Direct browser control tools - session routing
		elif tool_name.startswith('browser_'):
			# Extract session_id from arguments (don't pass to tool methods)
			session_id = arguments.pop('session_id', None)
			session = await self._get_session(session_id)

			async with session.session_lock:
				return await self._execute_tool_inner(tool_name, arguments, session)

		return f'Unknown tool: {tool_name}'

	async def _execute_tool_inner(self, tool_name: str, arguments: dict[str, Any], session: SessionState) -> str:
		"""Inner dispatch - routes tool calls to session-aware methods.

		Each method receives the session parameter for per-session isolation.
		"""
		if tool_name == 'browser_navigate':
			return await self._navigate(arguments['url'], arguments.get('new_tab', False), session=session)

		elif tool_name == 'browser_click':
			return await self._click(arguments['index'], arguments.get('new_tab', False), session=session)

		elif tool_name == 'browser_type':
			return await self._type_text(arguments['index'], arguments['text'], session=session)

		elif tool_name == 'browser_get_state':
			return await self._get_browser_state(arguments.get('include_screenshot', False), session=session)

		elif tool_name == 'browser_extract_content':
			return await self._extract_content(
				query=arguments['query'],
				extract_links=arguments.get('extract_links', False),
				skip_json_filtering=arguments.get('skip_json_filtering', False),
				start_from_char=arguments.get('start_from_char', 0),
				output_schema=arguments.get('output_schema'),
				session=session,
			)

		elif tool_name == 'browser_scroll':
			return await self._scroll(
				direction=arguments.get('direction', 'down'),
				pages=arguments.get('pages'),
				element_index=arguments.get('element_index'),
				session=session,
			)

		elif tool_name == 'browser_go_back':
			return await self._go_back(session=session)

		elif tool_name == 'browser_find_text':
			return await self._find_text(arguments['text'], session=session)

		elif tool_name == 'browser_send_keys':
			return await self._send_keys(arguments['keys'], session=session)

		elif tool_name == 'browser_evaluate':
			return await self._evaluate_js(arguments['expression'], session=session)

		elif tool_name == 'browser_list_tabs':
			return await self._list_tabs(session=session)

		elif tool_name == 'browser_switch_tab':
			return await self._switch_tab(arguments['tab_id'], session=session)

		elif tool_name == 'browser_close_tab':
			return await self._close_tab(arguments['tab_id'], session=session)

		return f'Unknown tool: {tool_name}'

	async def _init_browser_session(self, allowed_domains: list[str] | None = None, **kwargs):
		"""Initialize browser session using config.

		Uses a lock to prevent race conditions when multiple coroutines
		try to initialize the session concurrently.
		"""
		async with self._init_lock:
			if self.browser_session and self.tools and self.file_system:
				return

			# Ensure all logging goes to stderr before browser initialization
			_ensure_all_loggers_use_stderr()

			logger.debug('Initializing browser session...')

			# Get profile config
			profile_config = get_default_profile(self.config)

			# Merge profile config with defaults and overrides
			profile_data = {
				'downloads_path': str(Path.home() / 'Downloads' / 'browser-use-mcp'),
				'wait_between_actions': 0.5,
				'keep_alive': True,
				'user_data_dir': '~/.config/browseruse/profiles/default',
				'device_scale_factor': 1.0,
				'disable_security': False,
				'headless': False,
				**profile_config,  # Config values override defaults
			}

			# Tool parameter overrides (highest priority)
			if allowed_domains is not None:
				profile_data['allowed_domains'] = allowed_domains

			# Merge any additional kwargs that are valid BrowserProfile fields
			for key, value in kwargs.items():
				profile_data[key] = value

			# Create and start browser session if needed
			if not self.browser_session:
				profile = BrowserProfile(**profile_data)
				self.browser_session = BrowserSession(browser_profile=profile)
				await self.browser_session.start()

			# Create tools for direct actions (may be missing after partial init)
			if not self.tools:
				self.tools = Tools()

			# self.llm is already initialized in __init__ (haiku via openai-proxy)

			# Initialize FileSystem for extraction actions (may be missing after partial init)
			if not self.file_system:
				file_system_path = profile_config.get('file_system_path', '~/.browser-use-mcp')
				self.file_system = FileSystem(base_dir=Path(file_system_path).expanduser())

			logger.debug('Browser session initialized')

	async def _create_session(
		self,
		session_id: str | None = None,
		headless: bool | None = None,
		viewport: dict | None = None,
	) -> str:
		"""Create a new isolated browser session.
		
		Args:
			session_id: Optional session ID (auto-generated if omitted)
			headless: Whether browser should run headless
			viewport: Viewport dimensions {width, height}
			
		Returns:
			The session_id of the created session
		"""
		import uuid
		
		async with self._sessions_lock:
			# Check max sessions limit
			if len(self.sessions) >= self.max_sessions:
				raise RuntimeError(f'Maximum sessions limit ({self.max_sessions}) reached')
			
			# Generate session ID if not provided
			if session_id is None:
				session_id = str(uuid.uuid4())[:8]
			
			if session_id in self.sessions:
				raise RuntimeError(f'Session {session_id} already exists')
			
			# Get profile config
			profile_config = get_default_profile(self.config)
			profile = BrowserProfile(**profile_config) if profile_config else BrowserProfile()
			
			# Override with session-specific settings
			if headless is not None:
				profile.headless = headless
			if viewport is not None:
				profile.viewport = viewport
			
			# Create browser session with unique data dir per session
			browser_session = BrowserSession(browser_profile=profile)
			await browser_session.start()
			
			# Create tools and file system for this session
			tools = Tools()
			file_system_base = Path(profile_config.get('file_system_path', '~/.browser-use-mcp')).expanduser()
			session_file_path = file_system_base / 'sessions' / session_id
			file_system = FileSystem(base_dir=session_file_path)
			
			# Create session state
			now = time.time()
			session_state = SessionState(
				session_id=session_id,
				browser_session=browser_session,
				tools=tools,
				file_system=file_system,
				session_lock=asyncio.Lock(),
				created_at=now,
				last_activity=now,
			)
			
			self.sessions[session_id] = session_state
			
			# Set as default if first session
			if self.default_session_id is None:
				self.default_session_id = session_id
			
			logger.debug(f'Created session {session_id}')
			return session_id

	async def _get_session(self, session_id: str | None = None) -> SessionState:
		"""Get session by ID, or default session, or create default if none exists.
		
		Args:
			session_id: Session ID to lookup. If None, returns default session.
			
		Returns:
			SessionState for the requested session
			
		Raises:
			RuntimeError: If session_id provided but not found
		"""
		if session_id is not None:
			# Explicit session requested
			if session_id not in self.sessions:
				raise RuntimeError(f'Session {session_id} not found')
			return self.sessions[session_id]
		
		# No session_id - use default
		if self.default_session_id is not None and self.default_session_id in self.sessions:
			return self.sessions[self.default_session_id]
		
		# No default session - create one (backward compatibility)
		new_session_id = await self._create_session()
		return self.sessions[new_session_id]

	async def _retry_with_browser_use_agent(
		self,
		task: str,
		max_steps: int = 100,
		model: str | None = None,
		allowed_domains: list[str] | None = None,
		use_vision: bool = True,
	) -> str:
		"""Run an autonomous agent task with pattern learning, reusing the current browser session."""
		logger.debug(f'Running agent task: {task}')

		# Get LLM config
		llm_config = get_default_llm(self.config)

		# Get LLM provider - priority: config > env > auto-detect from model name
		model_provider = llm_config.get('model_provider') or os.getenv('MODEL_PROVIDER') or ''

		# Model priority: explicit parameter > config > env
		# No hardcoded fallback - let the provider selection logic handle defaults
		config_model = llm_config.get('model') or os.getenv('BROWSER_USE_AGENT_MODEL')
		llm_model = model or config_model

		if model_provider.lower() == 'bedrock':
			llm_model = llm_model or 'us.anthropic.claude-sonnet-4-20250514-v1:0'
			aws_region = llm_config.get('region') or os.getenv('REGION') or 'us-east-1'
			llm = ChatAWSBedrock(
				model=llm_model,
				aws_region=aws_region,
				aws_sso_auth=True,
			)
		elif model_provider.lower() in ('anthropic', 'claude') or (llm_model and llm_model.startswith('claude')):
			# Anthropic
			api_key = llm_config.get('api_key') or os.getenv('ANTHROPIC_API_KEY')
			if not api_key:
				return 'Error: ANTHROPIC_API_KEY not set in config or environment'
			llm = ChatAnthropic(
				model=llm_model or 'claude-sonnet-4-0',
				api_key=api_key,
				temperature=llm_config.get('temperature', 0.0),
			)
		elif model_provider.lower() in ('google', 'vertex') or (llm_model and llm_model.startswith('gemini')):
			# Google / Vertex AI
			google_kwargs: dict[str, Any] = {}
			if vertexai_flag := llm_config.get('vertexai') or os.getenv('GOOGLE_VERTEXAI'):
				google_kwargs['vertexai'] = vertexai_flag in (True, 'true', '1', 'True')
			if project := llm_config.get('project') or os.getenv('GOOGLE_CLOUD_PROJECT'):
				google_kwargs['project'] = project
			if location := llm_config.get('location') or os.getenv('GOOGLE_CLOUD_LOCATION'):
				google_kwargs['location'] = location
			if api_key := llm_config.get('api_key') or os.getenv('GOOGLE_API_KEY'):
				google_kwargs['api_key'] = api_key
			llm = ChatGoogle(
				model=llm_model or 'gemini-2.5-flash',
				temperature=llm_config.get('temperature', 0.5),
				**google_kwargs,
			)
		else:
			# OpenAI-compatible fallback (includes opencode-openai-proxy)
			api_key = llm_config.get('api_key') or os.getenv('OPENAI_API_KEY') or 'not-needed'
			base_url = llm_config.get('base_url') or os.getenv('OPENAI_PROXY_BASE_URL')
			provider = llm_config.get('provider') or os.getenv('BROWSER_USE_LLM_PROVIDER') or ''
			openai_kwargs: dict[str, Any] = {}
			if base_url:
				if provider:
					openai_kwargs['base_url'] = f'{base_url.rstrip("/")}/{provider}'
				else:
					openai_kwargs['base_url'] = base_url
			llm = ChatOpenAI(
				model=llm_model or 'gpt-4.1',
				api_key=api_key,
				temperature=llm_config.get('temperature', 0.7),
				**openai_kwargs,
			)

		# Ensure browser session and tools are fully initialized (handles partial init)
		await self._init_browser_session(allowed_domains=allowed_domains)

		# Resolve patterns path from config or default
		patterns_path = llm_config.get('patterns_path') or os.getenv('BROWSER_USE_PATTERNS_PATH') or None

		# Create agent with pattern learning, reusing the existing browser session
		agent = PatternLearningAgent(
			task=task,
			llm=llm,
			browser_session=self.browser_session,
			use_vision=use_vision,
			patterns_path=patterns_path,
			auto_learn=True,
			page_extraction_llm=self.llm,  # extraction LLM from __init__
		)

		try:
			history = await agent.run(max_steps=max_steps)

			# Format results
			results = []
			results.append(f'Task completed in {len(history.history)} steps')
			results.append(f'Success: {history.is_successful()}')

			# Report pattern learning results
			patterns_file = agent.patterns_path
			if patterns_file.exists():
				results.append(f'Patterns saved to: {patterns_file}')

			# Get final result if available
			final_result = history.final_result()
			if final_result:
				results.append(f'\nFinal result:\n{final_result}')

			# Include any errors
			errors = history.errors()
			if errors:
				results.append(f'\nErrors encountered:\n{json.dumps(errors, indent=2)}')

			# Include URLs visited
			urls = history.urls()
			if urls:
				# Filter out None values and convert to strings
				valid_urls = [str(url) for url in urls if url is not None]
				if valid_urls:
					results.append(f'\nURLs visited: {", ".join(valid_urls)}')

			return '\n'.join(results)

		except Exception as e:
			logger.error(f'Agent task failed: {e}', exc_info=True)
			return f'Agent task failed: {str(e)}'
		finally:
			# Don't close the browser session - it's shared with direct control tools
			# Only close the agent's internal state
			if hasattr(agent, '_agent'):
				agent._agent.browser_session = None  # Prevent agent.close() from closing our session
			await agent.close()

	async def _navigate(self, url: str, new_tab: bool = False, session: SessionState | None = None) -> str:
		"""Navigate to a URL."""
		# Support both session-based and legacy calls during transition
		bs = session.browser_session if session else self.browser_session
		if not bs:
			return 'Error: No browser session active'

		# Update session activity
		if session:
			session.last_activity = time.time()

		from browser_use.browser.events import NavigateToUrlEvent

		try:
			event = bs.event_bus.dispatch(NavigateToUrlEvent(url=url, new_tab=new_tab))
			await event
			# Wait for navigation to actually complete before returning
			await event.event_result(raise_if_any=True, raise_if_none=False)

			# Verify navigation actually succeeded
			actual_url = await bs.get_current_page_url()
			if actual_url == 'about:blank' and url != 'about:blank':
				return f'Navigation to {url} failed: page is still at about:blank. Browser session may be in an unstable state - try browser_close_session to close the stuck session and retry.'

			# Detect URL mismatch (redirect or failed SPA navigation)
			url_mismatch = actual_url and actual_url != url and not actual_url.rstrip('/').startswith(url.rstrip('/'))

			if new_tab:
				if url_mismatch:
					return f'Opened new tab. Requested: {url}, actual: {actual_url} (redirect or SPA routing)'
				return f'Opened new tab with URL: {actual_url}'
			else:
				if url_mismatch:
					return f'Navigated to: {actual_url} (requested: {url} - redirected)'
				return f'Navigated to: {actual_url}'
		except Exception as e:
			error_msg = str(e)
			logger.error(f'Navigation failed: {error_msg}')
			return f'Navigation to {url} failed: {error_msg}'

	async def _click(self, index: int, new_tab: bool = False, session: SessionState | None = None) -> str:
		"""Click an element by index with enriched response."""
		bs = session.browser_session if session else self.browser_session
		if not bs:
			return 'Error: No browser session active'

		# Update session activity
		if session:
			session.last_activity = time.time()

		# Get the element
		element = await bs.get_dom_element_by_index(index)
		if not element:
			return f'Element with index {index} not found'

		# Get element description for response
		from browser_use.tools.utils import get_click_description

		element_desc = get_click_description(element)

		# Capture tabs before click for new tab detection
		tabs_before = {t.target_id for t in await bs.get_tabs()}

		if new_tab:
			# For links, extract href and open in new tab
			href = element.attributes.get('href')
			if href:
				# Convert relative href to absolute URL
				state = await bs.get_browser_state_summary()
				current_url = state.url
				if href.startswith('/'):
					# Relative URL - construct full URL
					from urllib.parse import urlparse

					parsed = urlparse(current_url)
					full_url = f'{parsed.scheme}://{parsed.netloc}{href}'
				else:
					full_url = href

				# Open link in new tab
				from browser_use.browser.events import NavigateToUrlEvent

				event = bs.event_bus.dispatch(NavigateToUrlEvent(url=full_url, new_tab=True))
				await event
				await event.event_result(raise_if_any=True, raise_if_none=False)
				return f'Clicked {element_desc} (index {index}) | Opened in new tab: {full_url[:50]}...'
			else:
				# For non-link elements, just do a normal click
				from browser_use.browser.events import ClickElementEvent

				event = bs.event_bus.dispatch(ClickElementEvent(node=element))
				await event
				click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
				return await self._build_click_response(
					element_desc, index, click_metadata, tabs_before, session,
					'(new tab not supported for non-link elements)'
				)
		else:
			# Normal click
			from browser_use.browser.events import ClickElementEvent

			event = bs.event_bus.dispatch(ClickElementEvent(node=element))
			await event
			click_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
			return await self._build_click_response(element_desc, index, click_metadata, tabs_before, session)

	async def _build_click_response(
		self, element_desc: str, index: int, click_metadata: dict | None, tabs_before: set, session: SessionState, suffix: str = ''
	) -> str:
		"""Build enriched click response with metadata and new tab detection."""
		parts = [f'Clicked {element_desc} (index {index})']

		if suffix:
			parts.append(suffix)

		# Check for validation errors and download info from metadata
		if isinstance(click_metadata, dict):
			if click_metadata.get('validation_error'):
				parts.append(f"Warning: {click_metadata['validation_error']}")
			if click_metadata.get('download'):
				dl = click_metadata['download']
				parts.append(f"Downloaded: {dl.get('file_name', 'unknown')} ({dl.get('file_size', 0)} bytes)")
			if click_metadata.get('pdf_generated'):
				parts.append(f"Generated PDF: {click_metadata.get('path', '')}")

		# Detect new tabs opened
		tabs_after = {t.target_id for t in await session.browser_session.get_tabs()}
		new_tabs = tabs_after - tabs_before
		if new_tabs:
			for tid in new_tabs:
				tabs = await session.browser_session.get_tabs()
				for t in tabs:
					if t.target_id == tid:
						parts.append(f"Opened new tab: {t.url}")
						break

		return ' | '.join(parts)

	async def _type_text(self, index: int, text: str, session: SessionState) -> str:
		"""Type text into an element with enriched response."""
		element = await session.browser_session.get_dom_element_by_index(index)
		if not element:
			return f'Element with index {index} not found'

		from browser_use.browser.events import TypeTextEvent

		# Conservative heuristic to detect potentially sensitive data
		# Only flag very obvious patterns to minimize false positives
		is_potentially_sensitive = len(text) >= 6 and (
			# Email pattern: contains @ and a domain-like suffix
			('@' in text and '.' in text.split('@')[-1] if '@' in text else False)
			# Mixed alphanumeric with reasonable complexity (likely API keys/tokens)
			or (
				len(text) >= 16
				and any(char.isdigit() for char in text)
				and any(char.isalpha() for char in text)
				and any(char in '.-_' for char in text)
			)
		)

		# Use generic key names to avoid information leakage about detection patterns
		sensitive_key_name = None
		if is_potentially_sensitive:
			if '@' in text and '.' in text.split('@')[-1]:
				sensitive_key_name = 'email'
			else:
				sensitive_key_name = 'credential'

		event = session.browser_session.event_bus.dispatch(
			TypeTextEvent(node=element, text=text, is_sensitive=is_potentially_sensitive, sensitive_key_name=sensitive_key_name)
		)
		await event
		input_metadata = await event.event_result(raise_if_any=True, raise_if_none=False)

		# Build response
		if is_potentially_sensitive:
			if sensitive_key_name:
				return f'Typed <{sensitive_key_name}> into element {index}'
			else:
				return f'Typed <sensitive> into element {index}'
		else:
			parts = [f"Typed '{text}' into element {index}"]
			# Add actual value mismatch warning if available
			if isinstance(input_metadata, dict):
				actual_value = input_metadata.get('actual_value')
				if actual_value is not None and actual_value != text:
					parts.append(f"Warning: actual value is '{actual_value}' (may have autocomplete or formatting)")
			return ' | '.join(parts)

	async def _get_browser_state(self, include_screenshot: bool = False, session: SessionState | None = None) -> str:
		"""Get current browser state."""
		# Support both session-based and legacy calls during transition
		bs = session.browser_session if session else self.browser_session
		if not bs:
			return 'Error: No browser session active'

		state = await bs.get_browser_state_summary()

		result = {
			'url': state.url,
			'title': state.title,
			'tabs': [tab.model_dump(by_alias=True) for tab in state.tabs],
			'interactive_elements': [],
		}

		# Add interactive elements with their indices
		for index, element in state.dom_state.selector_map.items():
			elem_info = {
				'index': index,
				'tag': element.tag_name,
				'text': element.get_all_children_text(max_depth=2)[:100],
			}
			if element.attributes.get('placeholder'):
				elem_info['placeholder'] = element.attributes['placeholder']
			if element.attributes.get('href'):
				elem_info['href'] = element.attributes['href']
			result['interactive_elements'].append(elem_info)

		if include_screenshot and state.screenshot:
			result['screenshot'] = state.screenshot

		return json.dumps(result, indent=2)

	async def _extract_content(
		self,
		query: str,
		extract_links: bool = False,
		skip_json_filtering: bool = False,
		start_from_char: int = 0,
		output_schema: dict | None = None,
		session: SessionState | None = None,
	) -> str:
		"""Extract content from current page.

		Args:
			query: What information to extract from the page
			extract_links: Whether to include links in the extraction
			skip_json_filtering: Preserve JSON code blocks (useful for API docs)
			start_from_char: Start extraction from this character offset
			output_schema: Optional JSON Schema for structured extraction
			session: Session state (per-session browser, tools, file_system)
		"""
		# self.llm is always initialized in __init__ (haiku via openai-proxy)

		fs = session.file_system if session else self.file_system
		bs = session.browser_session if session else self.browser_session
		tools = session.tools if session else self.tools

		if not fs:
			return 'Error: FileSystem not initialized'

		if not bs:
			return 'Error: No browser session active'

		if not tools:
			return 'Error: Tools not initialized'

		state = await bs.get_browser_state_summary()

		# Use the extract action
		# Create a dynamic action model that matches the tools's expectations
		from pydantic import create_model

		# Create action model dynamically
		ExtractAction = create_model(
			'ExtractAction',
			__base__=ActionModel,
			extract=dict[str, Any],
		)

		# Build extract params
		extract_params: dict[str, Any] = {
			'query': query,
			'extract_links': extract_links,
			'skip_json_filtering': skip_json_filtering,
			'start_from_char': start_from_char,
		}
		if output_schema is not None:
			extract_params['output_schema'] = output_schema

		# Use model_validate because Pyright does not understand the dynamic model
		action = ExtractAction.model_validate({'extract': extract_params})
		action_result = await tools.act(
			action=action,
			browser_session=bs,
			page_extraction_llm=self.llm,
			file_system=fs,
		)

		content = action_result.extracted_content or 'No content extracted'

		# Add lazy loading hint if more content exists below current scroll position
		try:
			if state.page_info and state.page_info.pixels_below > 500:
				content += (
					f'\n\n[Note: {state.page_info.pixels_below}px of page content below current scroll position. '
					f'Use browser_scroll to load more.]'
				)
		except Exception:
			pass  # Don't fail extraction over a hint

		return content

	async def _scroll(self, direction: str = 'down', pages: float | None = None, element_index: int | None = None, session: SessionState | None = None) -> str:
		"""Scroll page or element with position feedback."""
		bs = session.browser_session if session else self.browser_session
		if not bs:
			return 'Error: No browser session active'

		if session:
			session.last_activity = time.time()

		from browser_use.browser.events import ScrollEvent

		# Determine scroll amount in pixels
		if pages is not None:
			# Get viewport height via CDP for accurate page-based scrolling
			try:
				cdp_session = await bs.get_or_create_cdp_session()
				metrics = await cdp_session.cdp_client.send.Page.getLayoutMetrics(session_id=cdp_session.session_id)
				viewport_height = metrics['cssVisualViewport']['clientHeight']
			except Exception:
				viewport_height = 1000  # Fallback same as Agent
			amount = int(pages * viewport_height)
		else:
			amount = 500  # Default - backward compatible

		# Resolve element node if specified
		node = None
		if element_index is not None:
			state = await bs.get_browser_state_summary()
			node = state.dom_state.selector_map.get(element_index)
			if node is None:
				return f'Element with index {element_index} not found'

		# Dispatch scroll event
		event = bs.event_bus.dispatch(
			ScrollEvent(direction=direction, amount=amount, node=node)  # type: ignore
		)
		await event

		# Get scroll position after scrolling
		try:
			state = await bs.get_browser_state_summary()
			if state.page_info:
				pi = state.page_info
				total_scrollable = max(pi.page_height - pi.viewport_height, 1)
				pct = round(pi.scroll_y / total_scrollable * 100)
				position = (
					f"scroll_y={pi.scroll_y}, "
					f"pixels_above={pi.pixels_above}, pixels_below={pi.pixels_below}, "
					f"scroll_percentage={pct}%"
				)
				target = f" element {element_index}" if element_index is not None else ""
				return f"Scrolled {direction}{target} | {position}"
		except Exception:
			pass

		target = f" element {element_index}" if element_index is not None else ""
		return f"Scrolled {direction}{target}"

	async def _go_back(self, session: SessionState) -> str:
		"""Go back in browser history."""
		from browser_use.browser.events import GoBackEvent

		event = session.browser_session.event_bus.dispatch(GoBackEvent())
		await event
		return 'Navigated back'

	async def _find_text(self, text: str, session: SessionState) -> str:
		"""Find text on page and scroll to it."""
		session.last_activity = time.time()

		from browser_use.browser.events import ScrollToTextEvent

		try:
			event = session.browser_session.event_bus.dispatch(ScrollToTextEvent(text=text))
			await event.event_result(raise_if_any=True, raise_if_none=False)
			return f"Found and scrolled to text: '{text}'"
		except Exception:
			return f"Text '{text}' not found or not visible on page"

	async def _send_keys(self, keys: str, session: SessionState) -> str:
		"""Send keyboard keys or shortcuts."""
		session.last_activity = time.time()

		from browser_use.browser.events import SendKeysEvent

		try:
			event = session.browser_session.event_bus.dispatch(SendKeysEvent(keys=keys))
			await event
			await event.event_result(raise_if_any=True, raise_if_none=False)
			return f'Sent keys: {keys}'
		except Exception as e:
			error_msg = str(e)
			logger.error(f'Send keys failed: {error_msg}')
			return f'Failed to send keys "{keys}": {error_msg}'

	async def _evaluate_js(self, expression: str, session: SessionState) -> str:
		"""Execute JavaScript on the current page and return the result."""
		session.last_activity = time.time()

		try:
			cdp_session = await session.browser_session.get_or_create_cdp_session()
			result = await cdp_session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': expression,
					'returnByValue': True,
					'awaitPromise': True,
				},
				session_id=cdp_session.session_id,
			)

			# Check for exceptions in the evaluation
			if 'exceptionDetails' in result:
				exception = result['exceptionDetails']
				error_text = exception.get('text', '')
				if 'exception' in exception:
					error_text = exception['exception'].get('description', error_text)
				return f'JavaScript error: {error_text}'

			# Extract the return value
			eval_result = result.get('result', {})
			value = eval_result.get('value')
			result_type = eval_result.get('type', 'undefined')

			if result_type == 'undefined':
				return 'JavaScript executed (no return value)'
			elif value is None:
				return 'null'
			elif isinstance(value, (dict, list)):
				return json.dumps(value, indent=2, ensure_ascii=False)
			else:
				return str(value)

		except Exception as e:
			error_msg = str(e)
			logger.error(f'JavaScript evaluation failed: {error_msg}')
			return f'JavaScript evaluation failed: {error_msg}'

	async def _list_tabs(self, session: SessionState) -> str:
		"""List all open tabs."""
		tabs_info = await session.browser_session.get_tabs()
		tabs = []
		for i, tab in enumerate(tabs_info):
			tabs.append({'tab_id': tab.target_id[-4:], 'url': tab.url, 'title': tab.title or ''})
		return json.dumps(tabs, indent=2)

	async def _switch_tab(self, tab_id: str, session: SessionState) -> str:
		"""Switch to a different tab."""
		from browser_use.browser.events import SwitchTabEvent

		target_id = await session.browser_session.get_target_id_from_tab_id(tab_id)
		event = session.browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
		await event
		state = await session.browser_session.get_browser_state_summary()
		return f'Switched to tab {tab_id}: {state.url}'

	async def _close_tab(self, tab_id: str, session: SessionState) -> str:
		"""Close a specific tab."""
		from browser_use.browser.events import CloseTabEvent

		target_id = await session.browser_session.get_target_id_from_tab_id(tab_id)
		event = session.browser_session.event_bus.dispatch(CloseTabEvent(target_id=target_id))
		await event
		current_url = await session.browser_session.get_current_page_url()
		return f'Closed tab # {tab_id}, now on {current_url}'

	async def _list_sessions(self) -> str:
		"""List all active browser sessions."""
		if not self.sessions:
			return 'No active browser sessions'

		sessions_info = []
		for session_id, session_state in self.sessions.items():
			bs = session_state.browser_session
			created_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_state.created_at))
			last_activity = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_state.last_activity))

			# Check if session is still active
			is_active = hasattr(bs, 'cdp_client') and bs.cdp_client is not None

			sessions_info.append(
				{
					'session_id': session_id,
					'created_at': created_at,
					'last_activity': last_activity,
					'is_default': session_id == self.default_session_id,
					'active': is_active,
					'age_minutes': round((time.time() - session_state.created_at) / 60, 1),
				}
			)

		return json.dumps(sessions_info, indent=2)

	async def _close_session(self, session_id: str | None = None) -> str:
		"""Close a browser session by ID. Without ID, list active sessions."""
		if session_id is None:
			if not self.sessions:
				return 'No active sessions.'
			session_list = []
			for sid, state in self.sessions.items():
				is_default = ' (default)' if sid == self.default_session_id else ''
				session_list.append(f'  {sid}{is_default}')
			return 'Specify session_id to close. Active sessions:\n' + '\n'.join(session_list)

		if session_id not in self.sessions:
			return f'Session {session_id} not found'

		session_state = self.sessions[session_id]
		bs = session_state.browser_session

		try:
			# Close the browser session
			if hasattr(bs, 'stop'):
				await bs.stop()
			elif hasattr(bs, 'kill'):
				await bs.kill()
			elif hasattr(bs, 'close'):
				await bs.close()

			# Remove from sessions dict
			del self.sessions[session_id]

			# If this was the default session, reassign default
			if self.default_session_id == session_id:
				self.default_session_id = next(iter(self.sessions), None)

			return f'Successfully closed session {session_id}'
		except Exception as e:
			return f'Error closing session {session_id}: {str(e)}'

	async def _cleanup_expired_sessions(self) -> None:
		"""Background task to clean up expired sessions."""
		current_time = time.time()
		timeout_seconds = self.session_timeout_minutes * 60

		expired_sessions = []
		for session_id, session_state in self.sessions.items():
			# Keep default session alive
			if session_id == self.default_session_id:
				continue
			if current_time - session_state.last_activity > timeout_seconds:
				expired_sessions.append(session_id)

		for session_id in expired_sessions:
			try:
				await self._close_session(session_id)
				logger.info(f'Auto-closed expired session {session_id}')
			except Exception as e:
				logger.error(f'Error auto-closing session {session_id}: {e}')

	async def _start_cleanup_task(self) -> None:
		"""Start the background cleanup task."""

		async def cleanup_loop():
			while True:
				try:
					await self._cleanup_expired_sessions()
					# Check every 2 minutes
					await asyncio.sleep(120)
				except Exception as e:
					logger.error(f'Error in cleanup task: {e}')
					await asyncio.sleep(120)

		self._cleanup_task = create_task_with_error_handling(cleanup_loop(), name='mcp_cleanup_loop', suppress_exceptions=True)

	async def run(self):
		"""Run the MCP server."""
		# Start the cleanup task
		await self._start_cleanup_task()

		async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
			await self.server.run(
				read_stream,
				write_stream,
				InitializationOptions(
					server_name='browser-use',
					server_version='0.1.0',
					capabilities=self.server.get_capabilities(
						notification_options=NotificationOptions(),
						experimental_capabilities={},
					),
				),
			)


async def main(session_timeout_minutes: int = 10):
	if not MCP_AVAILABLE:
		print('MCP SDK is required. Install with: pip install mcp', file=sys.stderr)
		sys.exit(1)

	server = BrowserUseServer(session_timeout_minutes=session_timeout_minutes)
	server._telemetry.capture(
		MCPServerTelemetryEvent(
			version=get_browser_use_version(),
			action='start',
			parent_process_cmdline=get_parent_process_cmdline(),
		)
	)

	try:
		await server.run()
	finally:
		duration = time.time() - server._start_time
		server._telemetry.capture(
			MCPServerTelemetryEvent(
				version=get_browser_use_version(),
				action='stop',
				duration_seconds=duration,
				parent_process_cmdline=get_parent_process_cmdline(),
			)
		)
		server._telemetry.flush()


if __name__ == '__main__':
	asyncio.run(main())
