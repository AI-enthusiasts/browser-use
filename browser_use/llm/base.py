"""
We have switched all of our code from langchain to openai.types.chat.chat_completion_message_param.

For easier transition we have
"""

from typing import Any, Protocol, TypeVar, overload, runtime_checkable

from pydantic import BaseModel

from browser_use.llm.messages import BaseMessage
from browser_use.llm.views import ChatInvokeCompletion

T = TypeVar('T', bound=BaseModel)


def strip_markdown_json(content: str | None) -> str:
	"""Strip markdown code blocks from JSON response.
	
	Some models (e.g., Claude Haiku 4.5) wrap JSON responses in ```json...``` blocks.
	This utility strips those blocks to allow proper JSON parsing.
	
	Args:
		content: The response content, possibly wrapped in markdown code blocks
		
	Returns:
		The content with markdown code blocks stripped, or empty string if None
	"""
	if not content:
		return ''
	if content.startswith('```json') and content.endswith('```'):
		return content[7:-3].strip()
	if content.startswith('```') and content.endswith('```'):
		return content[3:-3].strip()
	return content


@runtime_checkable
class BaseChatModel(Protocol):
	_verified_api_keys: bool = False

	model: str

	@property
	def provider(self) -> str: ...

	@property
	def name(self) -> str: ...

	@property
	def model_name(self) -> str:
		# for legacy support
		return self.model

	@overload
	async def ainvoke(
		self, messages: list[BaseMessage], output_format: None = None, **kwargs: Any
	) -> ChatInvokeCompletion[str]: ...

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]: ...

	async def ainvoke(
		self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
	) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]: ...

	@classmethod
	def __get_pydantic_core_schema__(
		cls,
		source_type: type,
		handler: Any,
	) -> Any:
		"""
		Allow this Protocol to be used in Pydantic models -> very useful to typesafe the agent settings for example.
		Returns a schema that allows any object (since this is a Protocol).
		"""
		from pydantic_core import core_schema

		# Return a schema that accepts any object for Protocol types
		return core_schema.any_schema()
