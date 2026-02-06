# Atomic File Write Patterns in Browser-Use

## INTENT
Find existing patterns for safe file writing in browser-use codebase to ensure consistency when fixing PatternStore.save()

## FINDINGS

### 1. EXISTING ATOMIC WRITE PATTERN (BEST PRACTICE)
**Location:** `browser_use/browser/watchdogs/storage_state_watchdog.py` (lines 203-213)

```python
# Write atomically
temp_path = json_path.with_suffix('.json.tmp')
temp_path.write_text(json.dumps(merged_state, indent=4))

# Backup existing file
if json_path.exists():
    backup_path = json_path.with_suffix('.json.bak')
    json_path.replace(backup_path)

# Move temp to final
temp_path.replace(json_path)
```

**Key Features:**
- Uses `.with_suffix()` to create temp file path
- Writes to temp file first
- Creates backup of existing file before replacement
- Uses `Path.replace()` for atomic rename (OS-level atomic operation)
- Wrapped in try-except for error handling

### 2. CURRENT PATTERNSTORE.SAVE() (NON-ATOMIC)
**Location:** `browser_use/agent/pattern_learning.py` (lines 205-220)

```python
def save(self, data: PatternFile) -> None:
    """Save patterns to file."""
    self.path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(self.path, 'w', encoding='utf-8') as f:
        json.dump(data.model_dump(), f, indent=2)
    
    self._cached_data = data
    logger.debug(f'Saved patterns to {self.path}')
```

**Issues:**
- Direct write to target file (not atomic)
- No error handling
- If interrupted, file could be corrupted/empty
- No backup of existing data

### 3. OTHER FILE WRITE PATTERNS IN CODEBASE

**AgentHistoryList.save_to_file()** - `browser_use/agent/views.py` (lines 620-629)
```python
def save_to_file(self, filepath: str | Path, sensitive_data: dict | None = None) -> None:
    try:
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(sensitive_data=sensitive_data)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        raise e
```
- Has error handling (try-except)
- Creates parent directories
- NOT atomic (direct write)

**Message Manager** - `browser_use/agent/message_manager/utils.py` (lines 27-29)
```python
await anyio.Path(target_path).write_text(
    await _format_conversation(input_messages, response),
    encoding=encoding or 'utf-8',
)
```
- Async file write
- Creates parent directories
- NOT atomic

## PYTHON BEST PRACTICES FOR ATOMIC WRITES

### Why Atomic Writes Matter
- **Data Loss Prevention:** If process crashes during write, original file remains intact
- **Corruption Prevention:** Partial writes don't corrupt existing data
- **Consistency:** Reader never sees incomplete/corrupted state

### Recommended Pattern (from Python community)
```python
import tempfile
from pathlib import Path

def atomic_write(filepath: Path, content: str, encoding: str = 'utf-8') -> None:
    """Write content to file atomically using temp file + rename."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to temp file in same directory (ensures same filesystem)
    temp_path = filepath.with_suffix(filepath.suffix + '.tmp')
    
    try:
        temp_path.write_text(content, encoding=encoding)
        
        # Backup existing file if it exists
        if filepath.exists():
            backup_path = filepath.with_suffix(filepath.suffix + '.bak')
            filepath.replace(backup_path)
        
        # Atomic rename (OS-level operation)
        temp_path.replace(filepath)
    except Exception as e:
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        raise
```

### Key Principles
1. **Write to temp file first** - in same directory (same filesystem)
2. **Use `Path.replace()` or `os.replace()`** - atomic at OS level
3. **Backup existing file** - before overwriting
4. **Clean up on error** - remove temp file if write fails
5. **Error handling** - wrap in try-except, re-raise or log

### Why `.replace()` is Atomic
- On POSIX systems: uses `rename()` syscall (atomic)
- On Windows: uses `ReplaceFile()` API (atomic)
- Guaranteed to be atomic at OS level

## RECOMMENDATION FOR PATTERNSTORE.SAVE()

Apply the atomic write pattern from `storage_state_watchdog.py`:

```python
def save(self, data: PatternFile) -> None:
    """Save patterns to file atomically.
    
    Creates parent directories if they don't exist.
    Uses atomic write (temp file + rename) to prevent corruption.
    
    Args:
        data: PatternFile to save.
    """
    self.path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write atomically
    temp_path = self.path.with_suffix('.json.tmp')
    
    try:
        # Write to temp file
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data.model_dump(), f, indent=2)
        
        # Backup existing file
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
        logger.error(f'Failed to save patterns: {e}')
        raise
```

## CONSISTENCY NOTES
- Matches existing pattern in `storage_state_watchdog.py`
- Uses same `.with_suffix()` approach for temp files
- Includes error handling and cleanup
- Maintains logging consistency
