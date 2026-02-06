# MCP LLM Configuration for browser_extract_content

## INTENT SUMMARY
Understanding how the LLM is configured for the `browser_extract_content` MCP tool, including initialization, model selection, and error handling.

---

## 1. LLM INITIALIZATION IN `BrowserUseServer.__init__`

**Location:** `browser_use/mcp/server.py`, lines 191-226

### Exact Code:
```python
def __init__(self, session_timeout_minutes: int = 10):
    # ... other init code ...
    
    # LLM for page content extraction (browser_extract_content)
    llm_config = get_default_llm(self.config)
    proxy_base_url = llm_config.get('base_url') or os.getenv('OPENAI_PROXY_BASE_URL') or 'http://localhost:8080/v1'
    
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
```

### Key Points:
- **Always initialized** in `__init__` (not lazy-loaded)
- **Type:** `ChatOpenAI` (OpenAI-compatible LLM wrapper)
- **Temperature:** Fixed at `0.0` (deterministic, no randomness)
- **API Key Fallback:** `'not-needed'` if no key provided (allows local proxy usage)

---

## 2. MODEL SELECTION PRIORITY

**Order of precedence:**
1. `llm_config.get('extraction_model')` — from config file (highest priority)
2. `os.getenv('BROWSER_USE_EXTRACTION_MODEL')` — environment variable
3. `llm_config.get('model')` — fallback to default LLM model

### Config Loading Chain:
```
load_browser_use_config()
  → CONFIG.load_config()
    → _load_config()
      → _get_default_llm()
        → db_config.llm (from config.json)
```

**Config file location:** `~/.config/browseruse/config.json` (or `BROWSER_USE_CONFIG_DIR`)

---

## 3. BASE URL / PROXY CONFIGURATION

**Priority:**
1. `llm_config.get('base_url')` — from config file
2. `os.getenv('OPENAI_PROXY_BASE_URL')` — environment variable
3. `'http://localhost:8080/v1'` — default local proxy

### Provider-based URL Construction
If `provider` is set (config or `BROWSER_USE_LLM_PROVIDER` env), the final URL becomes:
`{base_url.rstrip("/")}/{provider}` → e.g. `http://localhost:8080/v1/anthropic`

This supports proxies that route by provider in URL path (e.g. opencode-openai-proxy).
If provider is empty/not set, base_url is used as-is (backward compatible).

Applied in both:
- `BrowserUseServer.__init__` (extraction LLM)
- `_retry_with_browser_use_agent` (OpenAI-compatible fallback branch)

### Use Case:
- **Local proxy:** For running local LLM servers (Ollama, vLLM, etc.)
- **OpenAI API:** Set `OPENAI_PROXY_BASE_URL=https://api.openai.com/v1`
- **Custom provider:** Any OpenAI-compatible endpoint
- **Provider routing proxy:** Set `provider` in config to route via `{base_url}/{provider}`

---

## 4. ENVIRONMENT VARIABLES NEEDED

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `OPENAI_API_KEY` | API key for LLM provider | `'not-needed'` | No (unless using remote API) |
| `BROWSER_USE_EXTRACTION_MODEL` | Override extraction model | None | No |
| `OPENAI_PROXY_BASE_URL` | Override base URL | `http://localhost:8080/v1` | No |
| `BROWSER_USE_LLM_MODEL` | Override default LLM model | None | No |

### Config File Overrides:
```json
{
  "llm": {
    "default_llm": {
      "model": "gpt-4-turbo",
      "extraction_model": "gpt-4-mini",
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1"
    }
  }
}
```

---

## 5. EXTRACTION FLOW: `_extract_content` → `tools.act` → LLM Call

### Step 1: `_extract_content` (MCP server, lines 927-987)

```python
async def _extract_content(
    self,
    query: str,
    extract_links: bool = False,
    skip_json_filtering: bool = False,
    start_from_char: int = 0,
    output_schema: dict | None = None,
) -> str:
    # Validation checks
    if not self.file_system:
        return 'Error: FileSystem not initialized'
    if not self.browser_session:
        return 'Error: No browser session active'
    if not self.tools:
        return 'Error: Tools not initialized'
    
    # Get current page state
    state = await self.browser_session.get_browser_state_summary()
    
    # Create dynamic action model
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
    
    # Execute action via tools.act
    action = ExtractAction.model_validate({'extract': extract_params})
    action_result = await self.tools.act(
        action=action,
        browser_session=self.browser_session,
        page_extraction_llm=self.llm,  # ← PASSES self.llm HERE
        file_system=self.file_system,
    )
    
    # Return result or fallback message
    return action_result.extracted_content or 'No content extracted'
```

### Step 2: `tools.act` (tools/service.py, lines 2167-2233)

```python
async def act(
    self,
    action: ActionModel,
    browser_session: BrowserSession,
    page_extraction_llm: BaseChatModel | None = None,  # ← RECEIVES LLM
    sensitive_data: dict[str, str | dict[str, str]] | None = None,
    available_file_paths: list[str] | None = None,
    file_system: FileSystem | None = None,
    extraction_schema: dict | None = None,
) -> ActionResult:
    """Execute an action"""
    
    for action_name, params in action.model_dump(exclude_unset=True).items():
        if params is not None:
            # ... Laminar tracing setup ...
            try:
                result = await self.registry.execute_action(
                    action_name=action_name,
                    params=params,
                    browser_session=browser_session,
                    page_extraction_llm=page_extraction_llm,  # ← PASSES TO REGISTRY
                    file_system=file_system,
                    sensitive_data=sensitive_data,
                    available_file_paths=available_file_paths,
                    extraction_schema=extraction_schema,
                )
            except Exception as e:
                logger.error(f"Action '{action_name}' failed with error: {str(e)}")
                result = ActionResult(error=str(e))
            
            # Convert result to ActionResult
            if isinstance(result, str):
                return ActionResult(extracted_content=result)
            elif isinstance(result, ActionResult):
                return result
            elif result is None:
                return ActionResult()
    
    return ActionResult()
```

### Step 3: `extract` Action (tools/service.py, lines 946-1163)

The extract action is registered as a tool and receives `page_extraction_llm` parameter.

**Two extraction paths:**

#### Path A: Structured Extraction (with `output_schema`)
```python
if structured_model is not None:
    # ... build system prompt and schema ...
    response = await asyncio.wait_for(
        page_extraction_llm.ainvoke(
            [SystemMessage(content=system_prompt), UserMessage(content=prompt)],
            output_format=structured_model,
        ),
        timeout=120.0,
    )
    
    result_data: dict = response.completion.model_dump(mode='json')
    result_json = json.dumps(result_data)
    
    # Return structured result
    return ActionResult(
        extracted_content=f'<url>...<structured_result>{result_json}</structured_result>',
        long_term_memory=memory,
    )
```

#### Path B: Free-text Extraction (default)
```python
else:
    # ... build system prompt ...
    response = await asyncio.wait_for(
        page_extraction_llm.ainvoke(
            [SystemMessage(content=system_prompt), UserMessage(content=prompt)],
        ),
        timeout=120.0,
    )
    
    extracted_content = f'<url>...<result>{response.completion}</result>'
    
    return ActionResult(
        extracted_content=extracted_content,
        long_term_memory=memory,
    )
```

---

## 6. WHY "No content extracted" IS RETURNED

### Condition in `_extract_content`:
```python
return action_result.extracted_content or 'No content extracted'
```

### Triggers for "No content extracted":

| Cause | Source | Condition |
|-------|--------|-----------|
| **LLM returned empty** | `extract` action | `response.completion` is empty/None |
| **ActionResult is None** | `tools.act` | Returns `ActionResult()` with no `extracted_content` |
| **Exception in extract** | `extract` action | Raises `RuntimeError` → caught in `tools.act` → returns `ActionResult(error=...)` |
| **FileSystem not initialized** | `_extract_content` | Early return: `'Error: FileSystem not initialized'` |
| **Browser session not active** | `_extract_content` | Early return: `'Error: No browser session active'` |
| **Tools not initialized** | `_extract_content` | Early return: `'Error: Tools not initialized'` |
| **start_from_char exceeds content** | `extract` action | `chunks` is empty → returns `ActionResult(error=...)` |
| **LLM timeout** | `extract` action | `asyncio.wait_for` timeout (120s) → raises `TimeoutError` |
| **LLM API error** | `ChatOpenAI.ainvoke` | Network/auth error → exception → caught in `tools.act` |

### Detailed Error Handling in `extract` Action:

```python
try:
    response = await asyncio.wait_for(
        page_extraction_llm.ainvoke(...),
        timeout=120.0,
    )
    # ... process response ...
    return ActionResult(extracted_content=...)
except Exception as e:
    logger.debug(f'Error extracting content: {e}')
    raise RuntimeError(str(e))  # ← Re-raised as RuntimeError
```

Then in `tools.act`:
```python
except Exception as e:
    logger.error(f"Action '{action_name}' failed with error: {str(e)}")
    result = ActionResult(error=str(e))  # ← Caught here, returns ActionResult with error
```

Finally in `_extract_content`:
```python
return action_result.extracted_content or 'No content extracted'
# If action_result.extracted_content is None, returns fallback message
```

---

## 7. FALLBACK BEHAVIOR WHEN NO API KEY

### Scenario: No `OPENAI_API_KEY` set

```python
api_key=llm_config.get('api_key') or os.getenv('OPENAI_API_KEY') or 'not-needed'
```

**Result:** `api_key='not-needed'`

### What happens:
1. **ChatOpenAI is initialized** with `api_key='not-needed'`
2. **AsyncOpenAI client is created** with this key
3. **When `ainvoke` is called:**
   - If using **local proxy** (`http://localhost:8080/v1`): Works fine (proxy doesn't validate key)
   - If using **OpenAI API** (`https://api.openai.com/v1`): Fails with auth error
   - Error is caught in `tools.act` → returns `ActionResult(error='...')`
   - `_extract_content` returns `'No content extracted'`

### Recommendation:
- **For local LLM:** No API key needed, use default proxy
- **For OpenAI/remote:** Set `OPENAI_API_KEY` environment variable
- **For custom provider:** Set `OPENAI_PROXY_BASE_URL` and appropriate API key

---

## 8. SUMMARY TABLE

| Component | Value | Source | Configurable |
|-----------|-------|--------|--------------|
| **LLM Class** | `ChatOpenAI` | hardcoded | No |
| **Temperature** | `0.0` | hardcoded | No |
| **Model** | extraction_model → model | config/env | Yes |
| **API Key** | env/config → 'not-needed' | env/config | Yes |
| **Base URL** | env/config → localhost:8080 | env/config | Yes |
| **Timeout** | 120 seconds | hardcoded | No |
| **Max Retries** | 5 | hardcoded | No |

---

## 9. DEBUGGING CHECKLIST

When "No content extracted" is returned:

1. **Check initialization errors:**
   ```python
   # These return early with error messages
   if not self.file_system: return 'Error: FileSystem not initialized'
   if not self.browser_session: return 'Error: No browser session active'
   if not self.tools: return 'Error: Tools not initialized'
   ```

2. **Check LLM configuration:**
   ```bash
   echo $OPENAI_API_KEY
   echo $BROWSER_USE_EXTRACTION_MODEL
   echo $OPENAI_PROXY_BASE_URL
   ```

3. **Check config file:**
   ```bash
   cat ~/.config/browseruse/config.json | jq '.llm'
   ```

4. **Check page content:**
   - Is there actually content on the page?
   - Is the query specific enough?
   - Check `start_from_char` doesn't exceed content length

5. **Check LLM response:**
   - Enable debug logging: `BROWSER_USE_LOGGING_LEVEL=debug`
   - Look for `Error extracting content:` in logs
   - Check if LLM returned empty response

6. **Check network/proxy:**
   - If using local proxy: Is it running on `http://localhost:8080/v1`?
   - If using OpenAI: Is API key valid?
   - Check firewall/proxy settings
