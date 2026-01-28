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
from typing import TYPE_CHECKING
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

	Example:
	    PatternEntry(
	        actions=["click element with text 'Accept All'"],
	        last_success="2024-01-15"
	    )
	"""

	actions: list[str]
	last_success: str | None = None


class PatternFile(BaseModel):
	"""Root structure of patterns.json.

	Attributes:
	    version: Schema version for future migrations.
	    patterns: Nested dict of domain -> pattern_type -> PatternEntry.
	        Use "_global" as domain key for universal patterns.

	Example:
	    PatternFile(patterns={
	        "amazon.com": {
	            "cookie_consent": PatternEntry(actions=["click 'Accept All'"]),
	        },
	        "_global": {
	            "cookie_consent": PatternEntry(actions=["click 'Accept' or 'I Agree'"]),
	        }
	    })
	"""

	version: int = 1
	patterns: dict[str, dict[str, PatternEntry]] = Field(default_factory=dict)


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
		"""Save patterns to file.

		Creates parent directories if they don't exist.

		Args:
		    data: PatternFile to save.
		"""
		self.path.parent.mkdir(parents=True, exist_ok=True)

		with open(self.path, 'w', encoding='utf-8') as f:
			json.dump(data.model_dump(), f, indent=2)

		self._cached_data = data
		logger.debug(f'Saved patterns to {self.path}')

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
