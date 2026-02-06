"""Tests for skip_json_filtering parameter in markdown extraction."""

from browser_use.dom.markdown_extractor import _preprocess_markdown_content


class TestSkipJsonFiltering:
	"""Tests for skip_json_filtering parameter."""

	def test_json_removed_by_default(self):
		"""Large JSON blobs should be removed when skip_json_filtering=False (default)."""
		# Create a JSON blob > 100 chars
		json_blob = '{"key": "' + 'x' * 100 + '"}'
		content = f'Header\n{json_blob}\nFooter'

		filtered, chars_removed = _preprocess_markdown_content(content)

		assert json_blob not in filtered
		assert 'Header' in filtered
		assert 'Footer' in filtered
		assert chars_removed > 0

	def test_json_preserved_when_skip_enabled(self):
		"""Large JSON blobs should be preserved when skip_json_filtering=True."""
		# Create a JSON blob > 100 chars
		json_blob = '{"key": "' + 'x' * 100 + '"}'
		content = f'Header\n{json_blob}\nFooter'

		filtered, _ = _preprocess_markdown_content(content, skip_json_filtering=True)

		assert json_blob in filtered
		assert 'Header' in filtered
		assert 'Footer' in filtered

	def test_json_in_code_blocks_removed_by_default(self):
		"""JSON in code blocks should be removed when skip_json_filtering=False."""
		json_code_block = '`{"key":"value","nested":{"deep":"data"}}`'
		content = f'Header\n{json_code_block}\nFooter'

		filtered, _ = _preprocess_markdown_content(content)

		# The regex removes JSON in backticks
		assert json_code_block not in filtered

	def test_json_in_code_blocks_preserved_when_skip_enabled(self):
		"""JSON in code blocks should be preserved when skip_json_filtering=True."""
		json_code_block = '`{"key":"value","nested":{"deep":"data"}}`'
		content = f'Header\n{json_code_block}\nFooter'

		filtered, _ = _preprocess_markdown_content(content, skip_json_filtering=True)

		assert json_code_block in filtered

	def test_type_field_json_removed_by_default(self):
		"""JSON with $type fields (>100 chars) should be removed by default."""
		# Create JSON with $type field > 100 chars
		json_blob = '{"$type":"SomeType","data":"' + 'x' * 100 + '"}'
		content = f'Header\n{json_blob}\nFooter'

		filtered, _ = _preprocess_markdown_content(content)

		assert json_blob not in filtered

	def test_type_field_json_preserved_when_skip_enabled(self):
		"""JSON with $type fields should be preserved when skip_json_filtering=True."""
		json_blob = '{"$type":"SomeType","data":"' + 'x' * 100 + '"}'
		content = f'Header\n{json_blob}\nFooter'

		filtered, _ = _preprocess_markdown_content(content, skip_json_filtering=True)

		assert json_blob in filtered

	def test_small_json_always_preserved(self):
		"""Small JSON objects (<100 chars) should always be preserved."""
		small_json = '{"key": "value"}'
		content = f'Header\n{small_json}\nFooter'

		# Test with skip_json_filtering=False
		filtered_default, _ = _preprocess_markdown_content(content)
		assert small_json in filtered_default

		# Test with skip_json_filtering=True
		filtered_skip, _ = _preprocess_markdown_content(content, skip_json_filtering=True)
		assert small_json in filtered_skip

	def test_newline_compression_still_works_with_skip(self):
		"""Newline compression should still work when skip_json_filtering=True."""
		content = 'Header\n\n\n\n\nFooter'

		filtered, _ = _preprocess_markdown_content(content, skip_json_filtering=True)

		# Should have at most 3 consecutive newlines
		assert '\n\n\n\n' not in filtered
