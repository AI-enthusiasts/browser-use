# MCP browser_extract_content — Planned Improvements

## Текущее состояние
MCP tool `browser_extract_content` — упрощённая версия полного `extract` action.

### Отсутствующие параметры в MCP:
1. `start_from_char` — для пагинации длинного контента
2. `output_schema` — для structured JSON extraction

## Решение: Добавить оба параметра

### Изменения в server.py

**Tool registration** (line ~297):
```python
types.Tool(
    name='browser_extract_content',
    description='Extract structured content from the current page based on a query',
    inputSchema={
        'type': 'object',
        'properties': {
            'query': {'type': 'string', 'description': 'What information to extract from the page'},
            'extract_links': {
                'type': 'boolean',
                'description': 'Whether to include links in the extraction',
                'default': False,
            },
            'start_from_char': {
                'type': 'integer',
                'description': 'Start extraction from this character offset (for pagination of long content)',
                'default': 0,
            },
            'output_schema': {
                'type': 'object',
                'description': 'Optional JSON Schema. When provided, returns validated JSON matching this schema instead of free-text.',
                'default': None,
            },
        },
        'required': ['query'],
    },
),
```

**Handler** (line ~527):
```python
elif tool_name == 'browser_extract_content':
    return await self._extract_content(
        arguments['query'],
        arguments.get('extract_links', False),
        arguments.get('start_from_char', 0),
        arguments.get('output_schema'),
    )
```

**Method signature** (line ~901):
```python
async def _extract_content(
    self,
    query: str,
    extract_links: bool = False,
    start_from_char: int = 0,
    output_schema: dict | None = None,
) -> str:
```

**Dynamic model creation**:
```python
ExtractAction = create_model(
    'ExtractAction',
    __base__=ActionModel,
    extract=dict[str, Any],
)

action = ExtractAction.model_validate({
    'extract': {
        'query': query,
        'extract_links': extract_links,
        'start_from_char': start_from_char,
        'output_schema': output_schema,
    },
})
```

## Связанная логика

### extract_clean_markdown()
Путь: `browser_use/dom/markdown_extractor.py`

Функция извлекает чистый markdown из HTML страницы:
1. Получает HTML через browser_session
2. Конвертирует в markdown
3. Фильтрует шум (реклама, навигация, футеры)
4. Возвращает (content, content_stats)

### chunk_markdown_by_structure()
Разбивает markdown на структурные chunks:
- Max 100k chars per chunk
- Сохраняет структуру (заголовки, таблицы)
- Поддерживает `start_from_char` для пагинации
- Возвращает overlap_prefix для контекста (например, заголовки таблиц)

## Status: PENDING
Ожидает запроса на реализацию.
