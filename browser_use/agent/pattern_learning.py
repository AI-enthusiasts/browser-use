"""
Pattern Learning System for Browser-Use Agent.

This module provides functionality to remember and reuse UI interaction patterns
(cookie banners, login forms, search boxes) across sessions to accelerate agent execution.

Key components:
- PatternEntry: Single UI interaction pattern
- PatternFile: Root structure for patterns.json
- PatternStore: JSON persistence with load/save/merge operations
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
	from browser_use.filesystem.file_system import FileSystem

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_PATTERNS_PATH = './patterns/patterns.json'
ENV_VAR_NAME = 'BROWSER_USE_PATTERNS_PATH'
SESSION_PATTERNS_FILENAME = 'session_patterns.json'


class PatternEntry(BaseModel):
	"""Single UI interaction pattern.

	Attributes:
	    actions: List of action descriptions in natural language.
	    last_success: ISO date string (YYYY-MM-DD) of last successful use.
	    success: Whether this pattern came from a successful task completion.

	Example:
	    PatternEntry(
	        actions=["click element with text 'Accept All'"],
	        last_success="2024-01-15"
	    )
	"""

	actions: list[str]
	last_success: str | None = None
	success: bool = True


class WorkflowStep(BaseModel):
	"""Single step in a workflow pattern.

	Attributes:
	    environment_state: Description of page state when this step applies.
	    reasoning: Why this step is needed in the workflow.
	    action: Generalized action to take (may use placeholders like {{query}}).
	"""

	environment_state: str
	reasoning: str
	action: str


class WorkflowPattern(BaseModel):
	"""Multi-step workflow pattern extracted from successful agent sessions.

	Attributes:
	    id: Short identifier used as dict key (e.g., "product_search", "login_2fa").
	    description: Human-readable summary of what this workflow does.
	    steps: Ordered sequence of workflow steps.
	    domain: Website domain this applies to, or "_global" for site-independent.
	    last_success: ISO date string of last successful use.
	    success: Whether this workflow came from a successful task.
	"""

	id: str
	description: str
	steps: list[WorkflowStep]
	domain: str = '_global'
	last_success: str | None = None
	success: bool = True


class InducedWorkflows(BaseModel):
	"""LLM output wrapper for workflow induction.

	Used as output_format in ainvoke() call. The LLM returns
	a list of workflows extracted from the session history.
	An empty list means no reusable workflows were found.
	"""

	workflows: list[WorkflowPattern]


class PatternFile(BaseModel):
	"""Root structure of patterns.json.

	Attributes:
	    version: Schema version for future migrations.
	    patterns: Nested dict of domain -> pattern_type -> PatternEntry.
	        Use "_global" as domain key for universal patterns.
	    workflows: Nested dict of domain -> workflow_id -> WorkflowPattern.
	        Multi-step workflow patterns induced from successful sessions.

	Example:
	    PatternFile(patterns={
	        "amazon.com": {
	            "cookie_consent": PatternEntry(actions=["click 'Accept All'"]),
	        },
	    }, workflows={
	        "amazon.com": {
	            "product_search": WorkflowPattern(
	                id="product_search",
	                description="Search and filter products",
	                steps=[WorkflowStep(
	                    environment_state="Search page loaded",
	                    reasoning="Enter search query",
	                    action="Type query into search input and press Enter",
	                )],
	            ),
	        },
	    })
	"""

	version: int = 2
	patterns: dict[str, dict[str, PatternEntry]] = Field(default_factory=dict)
	workflows: dict[str, dict[str, WorkflowPattern]] = Field(default_factory=dict)


class PatternStore:
	"""JSON persistence for UI interaction patterns.

	Handles loading, saving, and merging patterns with 3-tier path resolution:
	1. Explicit path parameter
	2. BROWSER_USE_PATTERNS_PATH environment variable
	3. Default: ./patterns/patterns.json

	Args:
	    path: Optional explicit path to patterns.json file.

	Example:
	    store = PatternStore("./my_patterns.json")
	    patterns = store.load()
	    patterns.patterns["example.com"] = {"search": PatternEntry(actions=["type query", "press Enter"])}
	    store.save(patterns)
	"""

	def __init__(self, path: str | Path | None = None):
		self.path = self._resolve_path(path)
		self._cached_data: PatternFile | None = None

	def _resolve_path(self, explicit_path: str | Path | None) -> Path:
		"""Resolve patterns file path with priority.

		Priority (highest to lowest):
		1. Explicit parameter (if provided)
		2. Environment variable BROWSER_USE_PATTERNS_PATH (if set)
		3. Default: ./patterns/patterns.json

		Args:
		    explicit_path: Path passed to constructor, or None.

		Returns:
		    Resolved absolute Path to patterns file.
		"""
		if explicit_path is not None:
			path = Path(explicit_path)
		elif os.environ.get(ENV_VAR_NAME):
			path = Path(os.environ[ENV_VAR_NAME])
		else:
			path = Path(DEFAULT_PATTERNS_PATH)

		return path.expanduser().resolve()

	def load(self) -> PatternFile:
		"""Load patterns from file.

		Returns:
		    PatternFile with loaded patterns, or empty PatternFile if file doesn't exist.

		Raises:
		    ValueError: If file exists but contains invalid JSON or schema.
		"""
		if not self.path.exists():
			logger.debug(f'Patterns file not found at {self.path}, returning empty PatternFile')
			return PatternFile()

		try:
			with open(self.path, encoding='utf-8') as f:
				data = json.load(f)

			self._cached_data = PatternFile.model_validate(data)
			return self._cached_data

		except json.JSONDecodeError as e:
			raise ValueError(f'Invalid JSON in patterns file {self.path}: {e}') from e
		except ValidationError as e:
			raise ValueError(f'Invalid patterns file schema {self.path}: {e}') from e

	def save(self, data: PatternFile) -> None:
		"""Save patterns to file atomically.

		Creates parent directories if they don't exist.
		Uses atomic write (temp file + rename) to prevent corruption.

		Args:
		    data: PatternFile to save.

		Raises:
		    IOError: If file cannot be written.
		"""
		self.path.parent.mkdir(parents=True, exist_ok=True)

		# Write atomically using temp file + rename
		temp_path = self.path.with_suffix('.json.tmp')

		try:
			# Write to temp file
			with open(temp_path, 'w', encoding='utf-8') as f:
				json.dump(data.model_dump(), f, indent=2)

			# Backup existing file if it exists
			if self.path.exists():
				backup_path = self.path.with_suffix('.json.bak')
				self.path.replace(backup_path)

			# Atomic rename
			temp_path.replace(self.path)

			self._cached_data = data
			logger.debug(f'Saved patterns to {self.path}')
		except Exception as e:
			# Clean up temp file on error
			if temp_path.exists():
				temp_path.unlink()
			logger.error(f'Failed to save patterns to {self.path}: {e}')
			raise

	def merge_from_session(self, file_system: FileSystem) -> int:
		"""Merge patterns discovered during session into persistent storage.

		Reads session_patterns.json from the agent's FileSystem, merges with
		existing patterns, and saves to persistent storage.

		Args:
		    file_system: Agent's FileSystem instance containing session files.

		Returns:
		    Number of patterns added or updated.
		"""
		# Try to read session patterns from FileSystem
		try:
			session_file = file_system.files.get(SESSION_PATTERNS_FILENAME)
			if session_file is None:
				logger.debug('No session_patterns.json found in FileSystem')
				return 0

			session_content = session_file.content
			if not session_content.strip():
				return 0

			session_data = PatternFile.model_validate(json.loads(session_content))

		except (json.JSONDecodeError, ValidationError) as e:
			logger.warning(f'Invalid session patterns, skipping merge: {e}')
			return 0

		# Load existing patterns
		existing = self.load()

		# Merge patterns
		count = 0
		today = date.today().isoformat()

		for domain, domain_patterns in session_data.patterns.items():
			if domain not in existing.patterns:
				existing.patterns[domain] = {}

			for pattern_type, pattern_entry in domain_patterns.items():
				# Update last_success to today
				pattern_entry.last_success = today

				# Add or update pattern
				existing.patterns[domain][pattern_type] = pattern_entry
				count += 1
				logger.debug(f'Merged pattern: {domain}/{pattern_type}')

		# Save merged patterns
		if count > 0:
			self.save(existing)

		return count

	def merge_workflows(self, workflows: list[WorkflowPattern]) -> int:
		"""Merge induced workflows into persistent storage.

		Args:
		    workflows: List of WorkflowPattern objects from LLM induction.

		Returns:
		    Number of workflows added or updated.
		"""
		if not workflows:
			return 0

		existing = self.load()
		count = 0
		today = date.today().isoformat()

		for workflow in workflows:
			domain = workflow.domain or '_global'
			if domain not in existing.workflows:
				existing.workflows[domain] = {}

			workflow.last_success = today
			existing.workflows[domain][workflow.id] = workflow
			count += 1
			logger.debug(f'Merged workflow: {domain}/{workflow.id}')

		if count > 0:
			self.save(existing)

		return count

	@staticmethod
	def normalize_domain(url: str) -> str:
		"""Extract and normalize domain from URL.

		Strips 'www.' prefix and converts to lowercase.

		Args:
		    url: Full URL or domain string.

		Returns:
		    Normalized domain (e.g., "amazon.com").

		Example:
		    >>> PatternStore.normalize_domain('https://www.Amazon.COM/path')
		    'amazon.com'
		"""
		parsed = urlparse(url)
		domain = parsed.netloc if parsed.netloc else parsed.path.split('/')[0]
		domain = domain.lower()

		if domain.startswith('www.'):
			domain = domain[4:]

		return domain


# LLM instructions for pattern learning
PATTERN_LEARNING_INSTRUCTIONS = """
<pattern_learning>

## READING PATTERNS
At session start, check <available_file_paths> for patterns.json.
If exists, use read_file to load it ONCE at the beginning.

## APPLYING KNOWN PATTERNS
When you encounter a UI element matching a known pattern:
1. Execute the known action sequence IMMEDIATELY
2. Combine with your main action in the same step when possible
3. Domain-specific patterns override _global patterns

## DISCOVERING NEW PATTERNS
When you successfully interact with a REPEATING UI element, write to session_patterns.json:
```json
{
  "version": 1,
  "patterns": {
    "domain.com": {
      "pattern_type": {
        "actions": ["action description"],
        "last_success": null
      }
    }
  }
}
```

## WHAT TO RECORD
- Cookie/consent banners (cookie_consent)
- Login form selectors (login_form)
- Search box submission method (search_box)
- Pagination controls (pagination)
- Modal/popup close buttons (modal_close)

## WHAT NOT TO RECORD
- One-time data (prices, names, specific content)
- Element indices (they change between sessions)
- Session-specific tokens or IDs

## APPLYING WORKFLOWS
When patterns.json contains a "workflows" section, check for applicable workflows.
A workflow is a multi-step recipe — follow the steps in order when the task matches.

When you encounter a task matching a known workflow:
1. Check the workflow's environment_state for each step
2. Execute steps in order, adapting to the actual page state
3. Domain-specific workflows override _global workflows
4. If a step fails, fall back to normal agent behavior

Workflows complement single-action patterns — use both together.

</pattern_learning>
"""


# WORKFLOW_INDUCTION_PROMPT iteration guide:
#
# This prompt is the most likely part to need tuning. Common issues:
#
# 1. LLM extracts too many workflows from simple sessions
#    → Strengthen "Return EMPTY list" instruction
#    → Add minimum step count check before calling LLM
#
# 2. Workflows are too specific (contain exact text/data)
#    → Add more examples of good vs bad generalization
#    → Add "use placeholders like {{query}}" instruction
#
# 3. Workflows are too vague (just "navigate and click")
#    → Add "include environment_state for each step" emphasis
#    → Add concrete examples of good workflow steps
#
# Users can override this prompt via induction_prompt parameter:
#   PatternLearningAgent(induction_prompt="your custom prompt")
#
# Debug induction by checking logs:
#   logger.debug logs input (task + step count) and output (workflow count)
WORKFLOW_INDUCTION_PROMPT = """You are analyzing a successful browser automation session to extract reusable workflow patterns.

A workflow is a multi-step procedure that can be reused for similar tasks on the same website.

<session_task>
{task}
</session_task>

<session_steps>
{steps}
</session_steps>

<instructions>
Extract REUSABLE workflow patterns from this session.

GOOD WORKFLOWS:
- Solve a CATEGORY of tasks, not just this specific instance
- Reference element types/roles (e.g., "search input", "submit button"), not specific text
- Capture the SEQUENCE of steps, including what page state triggers each step
- Have 2+ steps (single actions are handled separately as patterns)

EXAMPLES OF WORKFLOWS:
- Login flows (enter credentials, submit, handle 2FA)
- Search-filter-select sequences
- Form filling procedures
- Navigation patterns (menu → submenu → target page)

DO NOT EXTRACT:
- Single-action patterns (already handled by PatternEntry)
- Task-specific data (specific search queries, product names)
- Error recovery steps that happened during this session

Return an EMPTY workflows list if no reusable workflows can be extracted.
</instructions>"""


class PatternLearningAgent:
	"""Wrapper around Agent that adds pattern learning capabilities.

	Injects pattern learning instructions into the agent's system message and
	provides methods to save discovered patterns after a session.

	Uses composition with __getattr__ delegation - all Agent attributes and methods
	are accessible directly on PatternLearningAgent.

	Args:
	    task: The task for the agent to perform.
	    llm: Language model to use.
	    patterns_path: Optional path to patterns.json file.
	    extend_system_message: Additional system message (appended after pattern instructions).
	    available_file_paths: Additional file paths for the agent.
	    induction_prompt: Custom prompt for workflow induction. If None, uses WORKFLOW_INDUCTION_PROMPT.
	    auto_learn: If True, automatically save patterns and induce workflows after
	        successful runs. Opt-in only — defaults to False.
	    **kwargs: All other arguments passed to Agent constructor.

	Example:
	    # Manual mode (default):
	    agent = PatternLearningAgent(task="...", llm=llm)
	    await agent.run()
	    agent.save_patterns()
	    await agent.induce_workflows()

	    # Auto-learning mode:
	    agent = PatternLearningAgent(task="...", llm=llm, auto_learn=True)
	    await agent.run()  # patterns + workflows saved automatically on success
	"""

	def __init__(
		self,
		task: str,
		llm: Any,
		patterns_path: str | Path | None = None,
		extend_system_message: str | None = None,
		available_file_paths: list[str] | None = None,
		induction_prompt: str | None = None,
		auto_learn: bool = False,
		**kwargs,
	):
		from browser_use.agent.service import Agent
		from browser_use.agent.views import AgentHistoryList

		self._store = PatternStore(patterns_path)
		self._induction_prompt = induction_prompt or WORKFLOW_INDUCTION_PROMPT
		self._auto_learn = auto_learn

		# Build combined system message: pattern instructions + user's extension
		combined_message = PATTERN_LEARNING_INSTRUCTIONS
		if extend_system_message:
			combined_message = f'{combined_message}\n{extend_system_message}' if combined_message else extend_system_message

		# Build available file paths: patterns file + user's paths
		combined_paths = available_file_paths.copy() if available_file_paths else []
		if self._store.path.exists():
			combined_paths.insert(0, str(self._store.path))

		# Create inner Agent with injected parameters
		self._agent: Agent = Agent(
			task=task,
			llm=llm,
			extend_system_message=combined_message if combined_message else None,
			available_file_paths=combined_paths if combined_paths else None,
			**kwargs,
		)

		# Store reference to AgentHistoryList for type hints
		self._AgentHistoryList = AgentHistoryList

	def __getattr__(self, name: str):
		"""Delegate attribute access to inner Agent.

		Allows PatternLearningAgent to be used as a drop-in replacement for Agent.
		All Agent attributes (task, history, browser_session, etc.) are accessible.
		"""
		return getattr(self._agent, name)

	async def run(self, **kwargs):
		"""Run the agent and return history.

		Delegates to inner Agent.run() with all provided arguments.
		When auto_learn=True, automatically saves patterns and induces
		workflows after a successful run.

		Returns:
		    AgentHistoryList with complete execution history.
		"""
		history = await self._agent.run(**kwargs)

		if self._auto_learn:
			await self._auto_learn_from_session()

		return history

	async def _auto_learn_from_session(self) -> None:
		"""Run pattern saving and workflow induction after a session.

		Called automatically when auto_learn=True. Both operations are
		success-gated internally — they skip if the task failed.
		Exceptions are caught and logged, never propagated to the caller.
		"""
		try:
			pattern_count = self.save_patterns()
			if pattern_count > 0:
				logger.info('Auto-learned %d patterns', pattern_count)
		except Exception as e:
			logger.warning('Auto-learn pattern save failed: %s', e)

		try:
			workflow_count = await self.induce_workflows()
			if workflow_count > 0:
				logger.info('Auto-induced %d workflows', workflow_count)
		except Exception as e:
			logger.warning('Auto-learn workflow induction failed: %s', e)

	def save_patterns(self, force: bool = False) -> int:
		"""Save patterns discovered during the session to persistent storage.

		Only saves patterns when the task completed successfully. Skips saving
		when the task is not done or failed, unless force=True.

		Args:
		    force: If True, save patterns regardless of task outcome.

		Returns:
		    Number of patterns added or updated. Returns 0 if skipped.

		Example:
		    await agent.run()
		    count = agent.save_patterns()
		    print(f"Saved {count} new patterns")
		"""
		if not force:
			if not self._agent.history.is_done():
				logger.warning('Skipping pattern save: task not completed')
				return 0
			if not self._agent.history.is_successful():
				logger.info('Skipping pattern save: task was not successful')
				return 0
		return self._store.merge_from_session(self._agent.file_system)

	async def induce_workflows(self, force: bool = False) -> int:
		"""Induce workflow patterns from session history via separate LLM call.

		Analyzes the agent's execution history after a successful run and
		extracts reusable multi-step workflow patterns using an LLM.

		Uses page_extraction_llm (or main LLM) for the induction call —
		this is a separate call outside the agent's context, so it does
		not pollute the agent's conversation history.

		Args:
		    force: If True, skip success and step count checks.

		Returns:
		    Number of workflows added or updated. Returns 0 if skipped.
		"""
		import asyncio

		from browser_use.llm.messages import SystemMessage

		if not force:
			if not self._agent.history.is_done():
				logger.warning('Skipping workflow induction: task not completed')
				return 0
			if not self._agent.history.is_successful():
				logger.info('Skipping workflow induction: task was not successful')
				return 0
			if self._agent.history.number_of_steps() < 3:
				logger.debug(
					'Skipping workflow induction: too few steps (%d)',
					self._agent.history.number_of_steps(),
				)
				return 0

		# Serialize history for LLM
		steps = self._agent.history.agent_steps()
		steps_text = '\n'.join(steps)
		task = self._agent.task

		# Build prompt
		prompt = self._induction_prompt.format(task=task, steps=steps_text)

		# Get LLM — page_extraction_llm is always non-None after Agent init
		llm = self._agent.settings.page_extraction_llm

		logger.debug('Inducing workflows from %d steps for task: %s', len(steps), task[:100])

		try:
			response = await asyncio.wait_for(
				llm.ainvoke(
					[SystemMessage(content=prompt)],
					output_format=InducedWorkflows,
				),
				timeout=120.0,
			)
			result: InducedWorkflows = response.completion
		except Exception as e:
			logger.warning('Workflow induction failed: %s', e)
			return 0

		if not result.workflows:
			logger.debug('No workflows induced from session')
			return 0

		logger.debug('Induced %d workflows', len(result.workflows))

		# Merge into store
		return self._store.merge_workflows(result.workflows)

	@property
	def patterns_path(self) -> Path:
		"""Path to the patterns.json file."""
		return self._store.path

	@property
	def agent(self):
		"""Access the inner Agent instance directly."""
		return self._agent
