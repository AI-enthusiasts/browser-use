# Pattern Learning Module - Development Guidelines

## Architecture

**PatternLearningAgent is a WRAPPER, not a subclass.**

```
PatternLearningAgent
    └── _agent: Agent  (composition via __getattr__)
    └── _store: PatternStore
```

All Agent attributes are accessible via `__getattr__` delegation. This means:
- `agent.task` → `agent._agent.task`
- `agent.history` → `agent._agent.history`
- `agent.run()` → `agent._agent.run()`

## Key Invariants

1. **Patterns stored OUTSIDE browseruse_agent_data/**
   - `browseruse_agent_data/` is wiped on every Agent init
   - Patterns persist in `./patterns/patterns.json` (or custom path)
   - Session patterns written to `session_patterns.json` inside FileSystem

2. **Path resolution priority (highest to lowest):**
   - Explicit `patterns_path` parameter
   - `BROWSER_USE_PATTERNS_PATH` environment variable
   - Default: `./patterns/patterns.json`

3. **Instructions injected via extend_system_message**
   - `PATTERN_LEARNING_INSTRUCTIONS` constant prepended
   - User's `extend_system_message` appended after

4. **Patterns file added to available_file_paths**
   - Only if file exists at init time
   - Allows LLM to read patterns via `read_file` action

## Testing Commands

```bash
# Run all pattern learning tests
uv run pytest tests/ci/test_pattern_learning.py -v

# Run with coverage
uv run pytest tests/ci/test_pattern_learning.py --cov=browser_use.agent.pattern_learning

# Lint
uv run ruff check browser_use/agent/pattern_learning.py
```

## Common Pitfalls

### 1. Don't store patterns in browseruse_agent_data
```python
# WRONG - will be deleted on next run
store = PatternStore("./browseruse_agent_data/patterns.json")

# CORRECT - persistent location
store = PatternStore("./patterns/patterns.json")
```

### 2. Don't forget to call save_patterns()
```python
agent = PatternLearningAgent(task="...", llm=llm)
await agent.run()
# Patterns are in session_patterns.json but NOT persisted yet

agent.save_patterns()  # NOW they're saved to patterns.json
```

### 3. Patterns file must exist for LLM to read it
```python
# If patterns.json doesn't exist, it won't be in available_file_paths
# LLM can still WRITE to session_patterns.json
# Call save_patterns() to create patterns.json for next session
```

### 4. Use normalize_domain for consistent keys
```python
# WRONG - inconsistent domain keys
patterns["www.Amazon.COM"] = {...}
patterns["amazon.com"] = {...}

# CORRECT - normalized
domain = PatternStore.normalize_domain("https://www.Amazon.COM/path")
# Returns: "amazon.com"
```

## File Structure

```
browser_use/agent/
├── pattern_learning.py    # Main module
│   ├── PatternEntry       # Pydantic model for single pattern
│   ├── PatternFile        # Pydantic model for patterns.json
│   ├── PatternStore       # JSON persistence
│   ├── PatternLearningAgent  # Wrapper class
│   └── PATTERN_LEARNING_INSTRUCTIONS  # LLM prompt
└── AGENTS.md              # This file

tests/ci/
└── test_pattern_learning.py  # 28 tests, 96% coverage
```

## Exports

From `browser_use`:
- `PatternLearningAgent`
- `PatternStore`

```python
from browser_use import PatternLearningAgent, PatternStore
```
