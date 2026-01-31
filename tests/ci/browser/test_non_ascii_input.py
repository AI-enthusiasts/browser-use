"""Tests for non-ASCII character handling in key code generation.

Verifies that Cyrillic, CJK, Arabic, and other non-ASCII characters
are correctly handled by the key code generation methods, producing
empty/zero values instead of invalid US keyboard codes.

Covers:
- DefaultActionWatchdog._is_non_ascii_char
- DefaultActionWatchdog._get_char_modifiers_and_vk
- DefaultActionWatchdog._get_key_code_for_char
- actor.utils.get_key_info (used by page.press() and send_keys)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus

from browser_use.actor.utils import get_key_info
from browser_use.browser.watchdogs.default_action_watchdog import DefaultActionWatchdog


@pytest.fixture
def watchdog():
	"""Create a DefaultActionWatchdog with minimal mocked dependencies."""
	mock_session = MagicMock()
	mock_session.logger = MagicMock()
	mock_event_bus = MagicMock(spec=EventBus)
	mock_event_bus.handlers = {}

	# Use model_construct to bypass Pydantic validation for unit testing
	wd = DefaultActionWatchdog.model_construct(
		event_bus=mock_event_bus,
		browser_session=mock_session,
	)
	return wd


class TestIsNonAsciiChar:
	"""Tests for _is_non_ascii_char helper."""

	def test_ascii_letters_are_not_non_ascii(self, watchdog):
		for char in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ':
			assert watchdog._is_non_ascii_char(char) is False, f'ASCII letter {char!r} should not be non-ASCII'

	def test_ascii_digits_are_not_non_ascii(self, watchdog):
		for char in '0123456789':
			assert watchdog._is_non_ascii_char(char) is False

	def test_ascii_special_chars_are_not_non_ascii(self, watchdog):
		for char in '!@#$%^&*()_+-=[]{}|;:\'",.<>?/`~ \t\n':
			assert watchdog._is_non_ascii_char(char) is False, f'ASCII special char {char!r} should not be non-ASCII'

	def test_cyrillic_lowercase_is_non_ascii(self, watchdog):
		for char in 'абвгдежзийклмнопрстуфхцчшщъыьэюя':
			assert watchdog._is_non_ascii_char(char) is True, f'Cyrillic {char!r} should be non-ASCII'

	def test_cyrillic_uppercase_is_non_ascii(self, watchdog):
		for char in 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ':
			assert watchdog._is_non_ascii_char(char) is True, f'Cyrillic {char!r} should be non-ASCII'

	def test_cjk_characters_are_non_ascii(self, watchdog):
		for char in '你好世界':
			assert watchdog._is_non_ascii_char(char) is True, f'CJK {char!r} should be non-ASCII'

	def test_arabic_characters_are_non_ascii(self, watchdog):
		for char in 'مرحبا':
			assert watchdog._is_non_ascii_char(char) is True, f'Arabic {char!r} should be non-ASCII'

	def test_single_codepoint_emoji_is_non_ascii(self, watchdog):
		# Single codepoint emoji (U+1F44D): len == 1 in Python 3, ord > 127
		assert watchdog._is_non_ascii_char('\U0001f44d') is True

	def test_multi_codepoint_emoji_is_not_single_char(self, watchdog):
		# Multi-codepoint emoji (e.g. flag, skin tone modifier): len > 1
		# _is_non_ascii_char returns False because len != 1
		family_emoji = '\U0001f468\u200d\U0001f469\u200d\U0001f467'  # family emoji
		assert watchdog._is_non_ascii_char(family_emoji) is False

	def test_empty_string_is_not_non_ascii(self, watchdog):
		assert watchdog._is_non_ascii_char('') is False

	def test_multi_char_string_is_not_non_ascii(self, watchdog):
		assert watchdog._is_non_ascii_char('ab') is False
		assert watchdog._is_non_ascii_char('кот') is False


class TestGetCharModifiersAndVk:
	"""Tests for _get_char_modifiers_and_vk with non-ASCII characters."""

	def test_cyrillic_lowercase_returns_zero_modifiers_and_vk(self, watchdog):
		"""Cyrillic chars should return (0, 0, char) — no VK code, no modifiers."""
		for char in 'абвгдежзийклмнопрстуфхцчшщъыьэюя':
			modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk(char)
			assert modifiers == 0, f'Cyrillic {char!r}: modifiers should be 0, got {modifiers}'
			assert vk_code == 0, f'Cyrillic {char!r}: vk_code should be 0, got {vk_code}'
			assert base_key == char, f'Cyrillic {char!r}: base_key should be {char!r}, got {base_key!r}'

	def test_cyrillic_uppercase_returns_zero_modifiers_and_vk(self, watchdog):
		"""Uppercase Cyrillic should also return (0, 0, char), NOT Shift modifier."""
		for char in 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ':
			modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk(char)
			assert modifiers == 0, f'Cyrillic {char!r}: modifiers should be 0, got {modifiers}'
			assert vk_code == 0, f'Cyrillic {char!r}: vk_code should be 0, got {vk_code}'
			assert base_key == char, f'Cyrillic {char!r}: base_key should be {char!r}, got {base_key!r}'

	def test_cjk_returns_zero_modifiers_and_vk(self, watchdog):
		for char in '你好世界':
			modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk(char)
			assert modifiers == 0
			assert vk_code == 0
			assert base_key == char

	def test_ascii_lowercase_still_works(self, watchdog):
		"""Verify ASCII lowercase letters still produce correct VK codes."""
		modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk('a')
		assert modifiers == 0
		assert vk_code == ord('A')  # VK code is uppercase
		assert base_key == 'a'

	def test_ascii_uppercase_still_works(self, watchdog):
		"""Verify ASCII uppercase letters still produce Shift modifier."""
		modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk('A')
		assert modifiers == 8  # Shift
		assert vk_code == ord('A')
		assert base_key == 'a'

	def test_shift_chars_still_work(self, watchdog):
		"""Verify shift characters like ! @ # still produce correct codes."""
		modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk('!')
		assert modifiers == 8  # Shift
		assert vk_code == 49  # '1' key
		assert base_key == '1'

	def test_digits_still_work(self, watchdog):
		modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk('5')
		assert modifiers == 0
		assert vk_code == ord('5')
		assert base_key == '5'

	def test_space_still_works(self, watchdog):
		modifiers, vk_code, base_key = watchdog._get_char_modifiers_and_vk(' ')
		assert modifiers == 0
		assert vk_code == 32
		assert base_key == ' '


class TestGetKeyCodeForChar:
	"""Tests for _get_key_code_for_char with non-ASCII characters."""

	def test_cyrillic_returns_empty_string(self, watchdog):
		"""Cyrillic chars should return '' — no key code on US keyboard."""
		for char in 'абвгдежзийклмнопрстуфхцчшщъыьэюя':
			code = watchdog._get_key_code_for_char(char)
			assert code == '', f'Cyrillic {char!r}: key code should be empty, got {code!r}'

	def test_cyrillic_uppercase_returns_empty_string(self, watchdog):
		for char in 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ':
			code = watchdog._get_key_code_for_char(char)
			assert code == '', f'Cyrillic {char!r}: key code should be empty, got {code!r}'

	def test_cjk_returns_empty_string(self, watchdog):
		for char in '你好世界':
			code = watchdog._get_key_code_for_char(char)
			assert code == ''

	def test_ascii_letters_return_key_codes(self, watchdog):
		"""Verify ASCII letters still produce KeyX codes."""
		assert watchdog._get_key_code_for_char('a') == 'KeyA'
		assert watchdog._get_key_code_for_char('z') == 'KeyZ'
		assert watchdog._get_key_code_for_char('A') == 'KeyA'
		assert watchdog._get_key_code_for_char('Z') == 'KeyZ'

	def test_digits_return_digit_codes(self, watchdog):
		assert watchdog._get_key_code_for_char('0') == 'Digit0'
		assert watchdog._get_key_code_for_char('9') == 'Digit9'

	def test_special_chars_return_correct_codes(self, watchdog):
		assert watchdog._get_key_code_for_char(' ') == 'Space'
		assert watchdog._get_key_code_for_char('.') == 'Period'
		assert watchdog._get_key_code_for_char(',') == 'Comma'
		assert watchdog._get_key_code_for_char('-') == 'Minus'
		assert watchdog._get_key_code_for_char('/') == 'Slash'


class TestNonAsciiKeyCodeRegression:
	"""Regression tests for the original bug: Cyrillic chars producing invalid key codes.

	Before the fix:
	- 'к' (Cyrillic) would match char.isupper()/islower() and produce VK code ord('К')=1050
	- _get_key_code_for_char('к') would return 'KeyК' (invalid — Chrome expects 'KeyA'-'KeyZ')
	- This caused React combobox components to ignore the input entirely
	"""

	def test_cyrillic_ka_does_not_produce_key_code(self, watchdog):
		"""'к' was the specific character that broke Yandex.Eda search."""
		assert watchdog._get_key_code_for_char('к') == ''
		modifiers, vk, base = watchdog._get_char_modifiers_and_vk('к')
		assert vk == 0  # NOT ord('К') == 1050

	def test_cyrillic_uppercase_ka_does_not_produce_shift(self, watchdog):
		"""'К' should NOT produce Shift modifier — it's not a US keyboard key."""
		modifiers, vk, base = watchdog._get_char_modifiers_and_vk('К')
		assert modifiers == 0  # NOT 8 (Shift)
		assert vk == 0

	def test_mixed_ascii_cyrillic_string_chars(self, watchdog):
		"""Simulate typing 'Hello Мир' — ASCII and Cyrillic mixed."""
		test_string = 'Hello Мир'
		for char in test_string:
			if ord(char) > 127:
				# Non-ASCII: should get empty/zero
				assert watchdog._get_key_code_for_char(char) == ''
				m, v, _ = watchdog._get_char_modifiers_and_vk(char)
				assert v == 0
			else:
				# ASCII: should get valid codes
				code = watchdog._get_key_code_for_char(char)
				assert code != '', f'ASCII char {char!r} should have a key code'


class TestGetKeyInfoUtils:
	"""Tests for actor.utils.get_key_info — used by page.press() and send_keys.

	This function maps key names to (code, windowsVirtualKeyCode) tuples.
	Non-ASCII characters should NOT produce 'KeyX' codes with Cyrillic X.
	"""

	def test_named_keys_still_work(self):
		assert get_key_info('Enter') == ('Enter', 13)
		assert get_key_info('Tab') == ('Tab', 9)
		assert get_key_info('Escape') == ('Escape', 27)
		assert get_key_info('ArrowDown') == ('ArrowDown', 40)
		assert get_key_info('Backspace') == ('Backspace', 8)

	def test_ascii_letter_produces_key_code(self):
		code, vk = get_key_info('a')
		assert code == 'KeyA'
		assert vk == 65

	def test_ascii_uppercase_produces_key_code(self):
		code, vk = get_key_info('Z')
		assert code == 'KeyZ'
		assert vk == 90

	def test_digit_produces_digit_code(self):
		code, vk = get_key_info('5')
		assert code == 'Digit5'
		assert vk == 53

	def test_cyrillic_does_not_produce_key_code(self):
		"""Cyrillic 'к' should NOT produce ('Keyк', ord('К'))."""
		code, vk = get_key_info('к')
		# Should fall through to fallback: (key, None)
		assert code == 'к'  # fallback returns the key itself
		assert vk is None  # no VK code — won't be sent to CDP

	def test_cyrillic_uppercase_does_not_produce_key_code(self):
		code, vk = get_key_info('К')
		assert code == 'К'
		assert vk is None

	def test_cjk_does_not_produce_key_code(self):
		code, vk = get_key_info('你')
		assert code == '你'
		assert vk is None
