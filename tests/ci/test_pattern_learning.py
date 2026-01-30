"""Tests for the pattern learning system."""

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from browser_use.agent.pattern_learning import (
	PATTERN_LEARNING_INSTRUCTIONS,
	SESSION_PATTERNS_FILENAME,
	WORKFLOW_INDUCTION_PROMPT,
	InducedWorkflows,
	PatternEntry,
	PatternFile,
	PatternLearningAgent,
	PatternStore,
	WorkflowPattern,
	WorkflowStep,
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
			assert result.version == 2
			assert result.patterns == {}
			assert result.workflows == {}

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


class TestSavePatternSuccessGating:
	"""Tests for save_patterns() success/failure gating."""

	def _create_agent_with_mock_history(self, tmp_dir: str, is_done: bool, is_successful: bool | None):
		"""Helper: create PatternLearningAgent with mocked history."""
		mock_llm = create_mock_llm()
		agent = PatternLearningAgent(
			task='Test task',
			llm=mock_llm,
			patterns_path=Path(tmp_dir) / 'patterns.json',
		)

		# Mock the history on the inner agent
		mock_history = MagicMock()
		mock_history.is_done.return_value = is_done
		mock_history.is_successful.return_value = is_successful
		agent._agent.history = mock_history

		return agent

	def _add_session_patterns(self, agent: PatternLearningAgent):
		"""Helper: add session patterns to agent's FileSystem so merge has data."""
		session_data = {
			'version': 1,
			'patterns': {
				'example.com': {
					'cookie_consent': {
						'actions': ["click 'Accept'"],
						'last_success': None,
					}
				}
			},
		}
		agent._agent.file_system.files[SESSION_PATTERNS_FILENAME] = JsonFile(
			name='session_patterns', content=json.dumps(session_data)
		)

	def test_save_patterns_skips_when_not_done(self):
		"""save_patterns() returns 0 when task is not completed."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=False, is_successful=None)
			self._add_session_patterns(agent)

			count = agent.save_patterns()

			assert count == 0

	def test_save_patterns_skips_when_not_successful(self):
		"""save_patterns() returns 0 when task completed but failed."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=False)
			self._add_session_patterns(agent)

			count = agent.save_patterns()

			assert count == 0

	def test_save_patterns_skips_when_success_is_none(self):
		"""save_patterns() returns 0 when is_successful() returns None (not done properly)."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=None)
			self._add_session_patterns(agent)

			count = agent.save_patterns()

			assert count == 0

	def test_save_patterns_saves_when_successful(self):
		"""save_patterns() saves patterns when task completed successfully."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=True)
			self._add_session_patterns(agent)

			count = agent.save_patterns()

			assert count == 1
			# Verify pattern was actually persisted
			loaded = agent._store.load()
			assert 'example.com' in loaded.patterns
			assert loaded.patterns['example.com']['cookie_consent'].actions == ["click 'Accept'"]

	def test_save_patterns_force_bypasses_gate(self):
		"""save_patterns(force=True) saves even when task is not done."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=False, is_successful=None)
			self._add_session_patterns(agent)

			count = agent.save_patterns(force=True)

			assert count == 1
			# Verify pattern was actually persisted
			loaded = agent._store.load()
			assert 'example.com' in loaded.patterns

	def test_pattern_entry_success_field_default(self):
		"""PatternEntry defaults success to True."""
		entry = PatternEntry(actions=['click button'])

		assert entry.success is True

	def test_pattern_entry_backward_compat(self):
		"""PatternEntry loads from JSON without success field (backward compat)."""
		data = {'actions': ["click 'Accept'"], 'last_success': '2024-01-15'}
		entry = PatternEntry.model_validate(data)

		assert entry.success is True
		assert entry.actions == ["click 'Accept'"]
		assert entry.last_success == '2024-01-15'


class TestWorkflowModels:
	"""Tests for workflow Pydantic models."""

	def test_workflow_step_validates(self):
		"""WorkflowStep with all fields validates."""
		step = WorkflowStep(
			environment_state='Search page loaded',
			reasoning='Enter search query',
			action='Type query into search input and press Enter',
		)
		assert step.environment_state == 'Search page loaded'
		assert step.reasoning == 'Enter search query'
		assert step.action == 'Type query into search input and press Enter'

	def test_workflow_pattern_validates(self):
		"""WorkflowPattern with defaults validates."""
		pattern = WorkflowPattern(
			id='product_search',
			description='Search and filter products',
			steps=[
				WorkflowStep(
					environment_state='Home page',
					reasoning='Navigate to search',
					action='Click search input',
				),
				WorkflowStep(
					environment_state='Search input focused',
					reasoning='Enter query',
					action='Type query and press Enter',
				),
			],
		)
		assert pattern.id == 'product_search'
		assert pattern.domain == '_global'
		assert pattern.last_success is None
		assert pattern.success is True
		assert len(pattern.steps) == 2

	def test_workflow_pattern_default_domain(self):
		"""WorkflowPattern domain defaults to '_global'."""
		pattern = WorkflowPattern(
			id='test',
			description='Test workflow',
			steps=[WorkflowStep(environment_state='s', reasoning='r', action='a')],
		)
		assert pattern.domain == '_global'

	def test_induced_workflows_empty_list(self):
		"""InducedWorkflows with empty list validates."""
		result = InducedWorkflows(workflows=[])
		assert result.workflows == []

	def test_induced_workflows_with_data(self):
		"""InducedWorkflows with workflow data validates."""
		result = InducedWorkflows(
			workflows=[
				WorkflowPattern(
					id='login',
					description='Login flow',
					steps=[WorkflowStep(environment_state='s', reasoning='r', action='a')],
					domain='example.com',
				)
			]
		)
		assert len(result.workflows) == 1
		assert result.workflows[0].id == 'login'


class TestWorkflowStorage:
	"""Tests for workflow persistence in PatternFile and PatternStore."""

	def test_pattern_file_v2_with_workflows(self):
		"""PatternFile with workflows round-trips through save/load."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(path)

			original = PatternFile(
				patterns={
					'example.com': {
						'cookie_consent': PatternEntry(actions=["click 'Accept'"]),
					}
				},
				workflows={
					'example.com': {
						'login_flow': WorkflowPattern(
							id='login_flow',
							description='Standard login',
							steps=[
								WorkflowStep(
									environment_state='Login page',
									reasoning='Enter credentials',
									action='Fill username and password fields',
								),
								WorkflowStep(
									environment_state='Credentials entered',
									reasoning='Submit form',
									action='Click submit button',
								),
							],
							domain='example.com',
							last_success='2024-06-01',
						),
					},
				},
			)

			store.save(original)
			loaded = store.load()

			assert loaded.version == 2
			assert 'example.com' in loaded.workflows
			assert 'login_flow' in loaded.workflows['example.com']
			wf = loaded.workflows['example.com']['login_flow']
			assert wf.description == 'Standard login'
			assert len(wf.steps) == 2
			assert wf.steps[0].environment_state == 'Login page'

	def test_pattern_file_v1_backward_compat(self):
		"""v1 JSON without workflows key loads fine."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			# Write v1 format (no workflows key)
			v1_data = {
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
			path.write_text(json.dumps(v1_data))

			store = PatternStore(path)
			loaded = store.load()

			assert 'example.com' in loaded.patterns
			assert loaded.workflows == {}

	def test_merge_workflows_adds_new(self):
		"""merge_workflows adds workflows to empty store."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			store = PatternStore(Path(tmp_dir) / 'patterns.json')

			workflows = [
				WorkflowPattern(
					id='search',
					description='Search flow',
					steps=[WorkflowStep(environment_state='s', reasoning='r', action='a')],
					domain='example.com',
				),
			]

			count = store.merge_workflows(workflows)

			assert count == 1
			loaded = store.load()
			assert 'example.com' in loaded.workflows
			assert 'search' in loaded.workflows['example.com']
			assert loaded.workflows['example.com']['search'].last_success == date.today().isoformat()

	def test_merge_workflows_updates_existing(self):
		"""merge_workflows overwrites existing workflow by domain/id."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(path)

			# Add initial workflow
			store.merge_workflows(
				[
					WorkflowPattern(
						id='login',
						description='Old login',
						steps=[WorkflowStep(environment_state='s', reasoning='r', action='old')],
						domain='site.com',
					),
				]
			)

			# Update with new version
			count = store.merge_workflows(
				[
					WorkflowPattern(
						id='login',
						description='New login',
						steps=[
							WorkflowStep(environment_state='s1', reasoning='r1', action='new1'),
							WorkflowStep(environment_state='s2', reasoning='r2', action='new2'),
						],
						domain='site.com',
					),
				]
			)

			assert count == 1
			loaded = store.load()
			wf = loaded.workflows['site.com']['login']
			assert wf.description == 'New login'
			assert len(wf.steps) == 2

	def test_merge_workflows_empty_list(self):
		"""merge_workflows([]) returns 0 and doesn't write."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			path = Path(tmp_dir) / 'patterns.json'
			store = PatternStore(path)

			count = store.merge_workflows([])

			assert count == 0
			assert not path.exists()


class TestInduceWorkflows:
	"""Tests for induce_workflows() method with mocked LLM."""

	def _create_agent_with_mock_history(
		self,
		tmp_dir: str,
		is_done: bool,
		is_successful: bool | None,
		num_steps: int = 5,
		induction_prompt: str | None = None,
	):
		"""Helper: create PatternLearningAgent with mocked history."""
		mock_llm = create_mock_llm()
		agent = PatternLearningAgent(
			task='Search for Python tutorials on example.com',
			llm=mock_llm,
			patterns_path=Path(tmp_dir) / 'patterns.json',
			induction_prompt=induction_prompt,
		)

		# Mock the history on the inner agent
		mock_history = MagicMock()
		mock_history.is_done.return_value = is_done
		mock_history.is_successful.return_value = is_successful
		mock_history.number_of_steps.return_value = num_steps
		mock_history.agent_steps.return_value = [
			'Step 1: Navigated to example.com',
			'Step 2: Clicked search input',
			'Step 3: Typed "Python tutorials"',
			'Step 4: Clicked first result',
			'Step 5: Extracted content',
		][:num_steps]
		agent._agent.history = mock_history

		return agent

	@pytest.mark.asyncio
	async def test_induce_workflows_skips_when_not_done(self):
		"""induce_workflows() returns 0 when task not completed, no LLM call."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=False, is_successful=None)

			count = await agent.induce_workflows()

			assert count == 0
			# LLM should not have been called for induction
			# (it was called once during Agent init for settings, but not for induction)

	@pytest.mark.asyncio
	async def test_induce_workflows_skips_when_not_successful(self):
		"""induce_workflows() returns 0 when task not successful."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=False)

			count = await agent.induce_workflows()

			assert count == 0

	@pytest.mark.asyncio
	async def test_induce_workflows_skips_few_steps(self):
		"""induce_workflows() returns 0 when < 3 steps."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=True, num_steps=2)

			count = await agent.induce_workflows()

			assert count == 0

	@pytest.mark.asyncio
	async def test_induce_workflows_calls_llm_on_success(self):
		"""induce_workflows() calls LLM and merges results on success."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=True, num_steps=5)

			# Mock the page_extraction_llm to return workflows
			from unittest.mock import AsyncMock

			from browser_use.llm.views import ChatInvokeCompletion

			mock_extraction_llm = AsyncMock()
			induced = InducedWorkflows(
				workflows=[
					WorkflowPattern(
						id='search_flow',
						description='Search and select result',
						steps=[
							WorkflowStep(environment_state='Home page', reasoning='Start search', action='Click search'),
							WorkflowStep(environment_state='Search focused', reasoning='Enter query', action='Type and submit'),
						],
						domain='example.com',
					),
				]
			)
			mock_extraction_llm.ainvoke.return_value = ChatInvokeCompletion(completion=induced, usage=None)
			agent._agent.settings.page_extraction_llm = mock_extraction_llm

			count = await agent.induce_workflows()

			assert count == 1
			mock_extraction_llm.ainvoke.assert_called_once()
			# Verify workflow was persisted
			loaded = agent._store.load()
			assert 'example.com' in loaded.workflows
			assert 'search_flow' in loaded.workflows['example.com']

	@pytest.mark.asyncio
	async def test_induce_workflows_force_bypasses_gate(self):
		"""induce_workflows(force=True) skips all checks."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=False, is_successful=None, num_steps=1)

			# Mock the page_extraction_llm to return empty workflows
			from unittest.mock import AsyncMock

			from browser_use.llm.views import ChatInvokeCompletion

			mock_extraction_llm = AsyncMock()
			induced = InducedWorkflows(workflows=[])
			mock_extraction_llm.ainvoke.return_value = ChatInvokeCompletion(completion=induced, usage=None)
			agent._agent.settings.page_extraction_llm = mock_extraction_llm

			count = await agent.induce_workflows(force=True)

			assert count == 0  # No workflows returned, but LLM was called
			mock_extraction_llm.ainvoke.assert_called_once()

	@pytest.mark.asyncio
	async def test_induce_workflows_handles_llm_error(self):
		"""induce_workflows() returns 0 on LLM exception."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			agent = self._create_agent_with_mock_history(tmp_dir, is_done=True, is_successful=True, num_steps=5)

			# Mock the page_extraction_llm to raise
			from unittest.mock import AsyncMock

			mock_extraction_llm = AsyncMock()
			mock_extraction_llm.ainvoke.side_effect = RuntimeError('LLM connection failed')
			agent._agent.settings.page_extraction_llm = mock_extraction_llm

			count = await agent.induce_workflows()

			assert count == 0

	@pytest.mark.asyncio
	async def test_induce_workflows_custom_prompt(self):
		"""Custom induction_prompt is used in LLM call."""
		with tempfile.TemporaryDirectory() as tmp_dir:
			custom_prompt = 'Custom induction prompt for task: {task}\nSteps: {steps}'
			agent = self._create_agent_with_mock_history(
				tmp_dir,
				is_done=True,
				is_successful=True,
				num_steps=5,
				induction_prompt=custom_prompt,
			)

			# Mock the page_extraction_llm
			from unittest.mock import AsyncMock

			from browser_use.llm.views import ChatInvokeCompletion

			mock_extraction_llm = AsyncMock()
			induced = InducedWorkflows(workflows=[])
			mock_extraction_llm.ainvoke.return_value = ChatInvokeCompletion(completion=induced, usage=None)
			agent._agent.settings.page_extraction_llm = mock_extraction_llm

			await agent.induce_workflows()

			# Verify the custom prompt was used
			call_args = mock_extraction_llm.ainvoke.call_args
			messages = call_args[0][0]  # First positional arg is messages list
			assert 'Custom induction prompt for task:' in messages[0].content
			assert 'Search for Python tutorials' in messages[0].content


class TestWorkflowInstructions:
	"""Tests for workflow section in PATTERN_LEARNING_INSTRUCTIONS."""

	def test_instructions_contain_workflow_section(self):
		"""PATTERN_LEARNING_INSTRUCTIONS has workflow content."""
		assert 'APPLYING WORKFLOWS' in PATTERN_LEARNING_INSTRUCTIONS
		assert 'workflows' in PATTERN_LEARNING_INSTRUCTIONS
		assert 'environment_state' in PATTERN_LEARNING_INSTRUCTIONS

	def test_induction_prompt_has_placeholders(self):
		"""WORKFLOW_INDUCTION_PROMPT has {task} and {steps} placeholders."""
		assert '{task}' in WORKFLOW_INDUCTION_PROMPT
		assert '{steps}' in WORKFLOW_INDUCTION_PROMPT

	def test_induction_prompt_formats(self):
		"""WORKFLOW_INDUCTION_PROMPT can be formatted with task and steps."""
		formatted = WORKFLOW_INDUCTION_PROMPT.format(
			task='Test task',
			steps='Step 1\nStep 2',
		)
		assert 'Test task' in formatted
		assert 'Step 1\nStep 2' in formatted
