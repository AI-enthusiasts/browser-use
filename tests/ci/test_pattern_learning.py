"""Tests for the pattern learning system."""

import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from browser_use.agent.pattern_learning import (
	PATTERN_LEARNING_INSTRUCTIONS,
	SESSION_PATTERNS_FILENAME,
	PatternEntry,
	PatternFile,
	PatternLearningAgent,
	PatternStore,
)
from browser_use.filesystem.file_system import FileSystem, JsonFile
from tests.ci.conftest import create_mock_llm


class TestPatternStore:
	"""Tests for PatternStore class."""

	def test_load_missing_file_returns_empty(self):
		"""Loading from non-existent file returns empty PatternFile."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			store = PatternStore(Path(tmp_dir) / 'nonexistent.json')
			result = store.load()

			assert isinstance(result, PatternFile)
			assert result.version == 1
			assert result.patterns == {}

	def test_load_valid_json(self):
		"""Loading valid JSON file returns PatternFile with data."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			data = {
				'version': 1,
				'patterns': {
					'example.com': {
						'cookie_consent': {
							'actions': ["click 'Accept'"],
							'last_success': '2024-01-15',
						}
					}
				},
			}
			path.write_text(json.dumps(data))

			store = PatternStore(path)
			result = store.load()

			assert result.version == 1
			assert 'example.com' in result.patterns
			assert result.patterns['example.com']['cookie_consent'].actions == ["click 'Accept'"]

	def test_load_invalid_json_raises(self):
		"""Loading invalid JSON raises ValueError."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			path.write_text('not valid json {{{')

			store = PatternStore(path)
			with pytest.raises(ValueError, match='Invalid JSON'):
				store.load()

	def test_load_invalid_schema_raises(self):
		"""Loading JSON with invalid schema raises ValueError."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			# Missing required 'actions' field in pattern entry
			data = {
				'version': 1,
				'patterns': {
					'example.com': {
						'cookie_consent': {
							'last_success': '2024-01-15',
							# 'actions' is missing
						}
					}
				},
			}
			path.write_text(json.dumps(data))

			store = PatternStore(path)
			with pytest.raises(ValueError, match='Invalid patterns file schema'):
				store.load()

	def test_save_creates_directories(self):
		"""Save creates parent directories if they don't exist."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'nested' / 'deep' / 'patterns.json'
			store = PatternStore(path)

			data = PatternFile(patterns={'test.com': {'login': PatternEntry(actions=['click login'])}})
			store.save(data)

			assert path.exists()
			assert path.parent.exists()

	def test_save_roundtrip(self):
		"""Data saved can be loaded back correctly."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(path)

			original = PatternFile(
				version=1,
				patterns={
					'amazon.com': {
						'cookie_consent': PatternEntry(actions=["click 'Accept All'"], last_success='2024-01-15'),
						'search_box': PatternEntry(actions=['type query', 'press Enter']),
					},
					'_global': {
						'modal_close': PatternEntry(actions=["click 'X' or 'Close'"]),
					},
				},
			)

			store.save(original)
			loaded = store.load()

			assert loaded.version == original.version
			assert loaded.patterns.keys() == original.patterns.keys()
			assert loaded.patterns['amazon.com']['cookie_consent'].actions == ["click 'Accept All'"]
			assert loaded.patterns['_global']['modal_close'].actions == ["click 'X' or 'Close'"]

	@pytest.mark.parametrize(
		'url,expected',
		[
			('https://www.Amazon.COM/path', 'amazon.com'),
			('https://amazon.com', 'amazon.com'),
			('http://WWW.Example.ORG/page', 'example.org'),
			('https://subdomain.example.com', 'subdomain.example.com'),
			('example.com', 'example.com'),
			('www.test.io', 'test.io'),
		],
	)
	def test_normalize_domain(self, url: str, expected: str):
		"""Domain normalization strips www and lowercases."""
		assert PatternStore.normalize_domain(url) == expected


class TestPatternStoreMerge:
	"""Tests for PatternStore.merge_from_session()."""

	def test_merge_adds_new_patterns(self):
		"""Merge adds patterns from session to persistent storage."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(patterns_path)

			# Create mock FileSystem with session patterns
			file_system = FileSystem(tmp_dir)
			session_data = {
				'version': 1,
				'patterns': {
					'newsite.com': {
						'cookie_consent': {
							'actions': ["click 'Accept'"],
							'last_success': None,
						}
					}
				},
			}
			file_system.files[SESSION_PATTERNS_FILENAME] = JsonFile(name='session_patterns', content=json.dumps(session_data))

			count = store.merge_from_session(file_system)

			assert count == 1
			loaded = store.load()
			assert 'newsite.com' in loaded.patterns
			assert loaded.patterns['newsite.com']['cookie_consent'].last_success == date.today().isoformat()

	def test_merge_updates_existing_patterns(self):
		"""Merge updates existing patterns with new data."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(patterns_path)

			# Create existing patterns
			existing = PatternFile(
				patterns={
					'example.com': {
						'cookie_consent': PatternEntry(actions=['old action'], last_success='2024-01-01'),
					}
				}
			)
			store.save(existing)

			# Create mock FileSystem with updated session patterns
			file_system = FileSystem(tmp_dir)
			session_data = {
				'version': 1,
				'patterns': {
					'example.com': {
						'cookie_consent': {
							'actions': ['new action'],
							'last_success': None,
						}
					}
				},
			}
			file_system.files[SESSION_PATTERNS_FILENAME] = JsonFile(name='session_patterns', content=json.dumps(session_data))

			count = store.merge_from_session(file_system)

			assert count == 1
			loaded = store.load()
			assert loaded.patterns['example.com']['cookie_consent'].actions == ['new action']
			assert loaded.patterns['example.com']['cookie_consent'].last_success == date.today().isoformat()

	def test_merge_no_session_file_returns_zero(self):
		"""Merge returns 0 when no session_patterns.json exists."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(patterns_path)

			# Create mock FileSystem without session patterns
			file_system = FileSystem(tmp_dir)

			count = store.merge_from_session(file_system)

			assert count == 0

	def test_merge_invalid_session_json_returns_zero(self):
		"""Merge returns 0 and logs warning for invalid session JSON."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(patterns_path)

			# Create mock FileSystem with invalid JSON
			file_system = FileSystem(tmp_dir)
			file_system.files[SESSION_PATTERNS_FILENAME] = JsonFile(name='session_patterns', content='not valid json')

			count = store.merge_from_session(file_system)

			assert count == 0

	def test_merge_empty_session_file_returns_zero(self):
		"""Merge returns 0 for empty session file."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(patterns_path)

			# Create mock FileSystem with empty content
			file_system = FileSystem(tmp_dir)
			file_system.files[SESSION_PATTERNS_FILENAME] = JsonFile(name='session_patterns', content='   ')

			count = store.merge_from_session(file_system)

			assert count == 0


class TestPatternLearningAgent:
	"""Tests for PatternLearningAgent class."""

	def test_init_creates_agent_with_instructions(self):
		"""PatternLearningAgent injects PATTERN_LEARNING_INSTRUCTIONS into agent."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=Path(tmp_dir) / 'patterns.json',
			)

			# Check that instructions are in the agent's settings
			assert '<pattern_learning>' in agent._agent.settings.extend_system_message

	def test_init_adds_patterns_to_available_paths(self):
		"""PatternLearningAgent adds existing patterns file to available_file_paths."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			# Create the patterns file so it gets added to available paths
			patterns_path.write_text('{"version": 1, "patterns": {}}')

			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=patterns_path,
			)

			# Check that patterns path is in available_file_paths
			assert str(patterns_path) in agent._agent.available_file_paths

	def test_init_preserves_user_available_paths(self):
		"""PatternLearningAgent preserves user-provided available_file_paths."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'patterns.json'
			patterns_path.write_text('{"version": 1, "patterns": {}}')
			user_path = '/some/user/file.txt'

			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=patterns_path,
				available_file_paths=[user_path],
			)

			# Check both paths are present
			assert str(patterns_path) in agent._agent.available_file_paths
			assert user_path in agent._agent.available_file_paths

	def test_getattr_delegates_to_agent(self):
		"""PatternLearningAgent delegates attribute access to inner Agent."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task for delegation',
				llm=mock_llm,
				patterns_path=Path(tmp_dir) / 'patterns.json',
			)

			# Access delegated attributes
			assert agent.task == 'Test task for delegation'
			assert hasattr(agent, 'history')
			assert hasattr(agent, 'file_system')

	def test_user_extend_message_preserved(self):
		"""User's extend_system_message is appended after pattern instructions."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			mock_llm = create_mock_llm()
			user_message = 'Custom user instructions here'

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=Path(tmp_dir) / 'patterns.json',
				extend_system_message=user_message,
			)

			# Check both pattern instructions and user message are present
			combined = agent._agent.settings.extend_system_message
			assert '<pattern_learning>' in combined
			assert user_message in combined
			# User message should come after pattern instructions
			assert combined.index('<pattern_learning>') < combined.index(user_message)

	def test_save_patterns_returns_int(self):
		"""save_patterns() returns integer count."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=Path(tmp_dir) / 'patterns.json',
			)

			# No session patterns, should return 0
			count = agent.save_patterns()
			assert isinstance(count, int)
			assert count == 0

	def test_patterns_path_property(self):
		"""patterns_path property returns correct Path."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			patterns_path = Path(tmp_dir) / 'my_patterns.json'
			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=patterns_path,
			)

			assert agent.patterns_path == patterns_path.resolve()

	def test_agent_property(self):
		"""agent property returns inner Agent instance."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			mock_llm = create_mock_llm()

			agent = PatternLearningAgent(
				task='Test task',
				llm=mock_llm,
				patterns_path=Path(tmp_dir) / 'patterns.json',
			)

			from browser_use.agent.service import Agent

			assert isinstance(agent.agent, Agent)
			assert agent.agent is agent._agent


class TestPatternLearningInstructions:
	"""Tests for PATTERN_LEARNING_INSTRUCTIONS constant."""

	def test_instructions_contain_xml_tags(self):
		"""Instructions are wrapped in <pattern_learning> tags."""
		assert '<pattern_learning>' in PATTERN_LEARNING_INSTRUCTIONS
		assert '</pattern_learning>' in PATTERN_LEARNING_INSTRUCTIONS

	def test_instructions_contain_key_sections(self):
		"""Instructions contain all required sections."""
		assert 'READING PATTERNS' in PATTERN_LEARNING_INSTRUCTIONS
		assert 'APPLYING KNOWN PATTERNS' in PATTERN_LEARNING_INSTRUCTIONS
		assert 'DISCOVERING NEW PATTERNS' in PATTERN_LEARNING_INSTRUCTIONS
		assert 'WHAT TO RECORD' in PATTERN_LEARNING_INSTRUCTIONS
		assert 'WHAT NOT TO RECORD' in PATTERN_LEARNING_INSTRUCTIONS

	def test_instructions_mention_session_patterns_file(self):
		"""Instructions mention session_patterns.json for writing."""
		assert 'session_patterns.json' in PATTERN_LEARNING_INSTRUCTIONS
