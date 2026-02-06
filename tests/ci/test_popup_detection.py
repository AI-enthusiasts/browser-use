"""Tests for popup/modal detection in HTMLSerializer."""

from browser_use.dom.serializer.html_serializer import HTMLSerializer
from browser_use.dom.views import EnhancedDOMTreeNode, NodeType


def create_mock_node(
	node_type: NodeType = NodeType.ELEMENT_NODE,
	node_name: str = 'div',
	attributes: dict | None = None,
	children: list | None = None,
) -> EnhancedDOMTreeNode:
	"""Create a minimal mock EnhancedDOMTreeNode for testing."""
	node = EnhancedDOMTreeNode(
		node_id=1,
		backend_node_id=1,
		node_type=node_type,
		node_name=node_name,
		node_value='',
		attributes=attributes or {},
		is_scrollable=None,
		is_visible=True,
		absolute_position=None,
		target_id='target',
		frame_id=None,
		session_id=None,
		content_document=None,
		shadow_root_type=None,
		shadow_roots=None,
		parent_node=None,
		children_nodes=children or [],
		ax_node=None,
		snapshot_node=None,
	)
	return node


class TestIsPopup:
	"""Tests for HTMLSerializer._is_popup method."""

	def test_dialog_open_is_popup(self):
		"""<dialog open> should be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='dialog', attributes={'open': ''})
		assert serializer._is_popup(node) is True

	def test_dialog_without_open_not_popup(self):
		"""<dialog> without open attribute should not be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='dialog', attributes={})
		assert serializer._is_popup(node) is False

	def test_role_dialog_is_popup(self):
		"""Element with role="dialog" should be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='div', attributes={'role': 'dialog'})
		assert serializer._is_popup(node) is True

	def test_role_alertdialog_is_popup(self):
		"""Element with role="alertdialog" should be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='div', attributes={'role': 'alertdialog'})
		assert serializer._is_popup(node) is True

	def test_aria_modal_true_is_popup(self):
		"""Element with aria-modal="true" should be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='div', attributes={'aria-modal': 'true'})
		assert serializer._is_popup(node) is True

	def test_aria_modal_false_not_popup(self):
		"""Element with aria-modal="false" should not be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='div', attributes={'aria-modal': 'false'})
		assert serializer._is_popup(node) is False

	def test_regular_div_not_popup(self):
		"""Regular div should not be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_name='div', attributes={'class': 'content'})
		assert serializer._is_popup(node) is False

	def test_text_node_not_popup(self):
		"""Text nodes should not be detected as popup."""
		serializer = HTMLSerializer()
		node = create_mock_node(node_type=NodeType.TEXT_NODE, node_name='#text')
		assert serializer._is_popup(node) is False


class TestDetectPopups:
	"""Tests for HTMLSerializer.detect_popups method."""

	def test_finds_single_popup(self):
		"""Should find a single popup in the tree."""
		serializer = HTMLSerializer()

		popup = create_mock_node(node_name='dialog', attributes={'open': ''})
		regular = create_mock_node(node_name='div', attributes={'class': 'content'})

		root = create_mock_node(
			node_type=NodeType.DOCUMENT_NODE,
			node_name='#document',
			children=[regular, popup],
		)

		popups = serializer.detect_popups(root)
		assert len(popups) == 1
		assert popups[0] is popup

	def test_finds_multiple_popups(self):
		"""Should find multiple popups in the tree."""
		serializer = HTMLSerializer()

		popup1 = create_mock_node(node_name='dialog', attributes={'open': ''})
		popup2 = create_mock_node(node_name='div', attributes={'role': 'dialog'})
		regular = create_mock_node(node_name='div', attributes={'class': 'content'})

		root = create_mock_node(
			node_type=NodeType.DOCUMENT_NODE,
			node_name='#document',
			children=[regular, popup1, popup2],
		)

		popups = serializer.detect_popups(root)
		assert len(popups) == 2

	def test_no_popups_returns_empty(self):
		"""Should return empty list when no popups found."""
		serializer = HTMLSerializer()

		regular1 = create_mock_node(node_name='div', attributes={'class': 'header'})
		regular2 = create_mock_node(node_name='div', attributes={'class': 'content'})

		root = create_mock_node(
			node_type=NodeType.DOCUMENT_NODE,
			node_name='#document',
			children=[regular1, regular2],
		)

		popups = serializer.detect_popups(root)
		assert len(popups) == 0


class TestSerializeExcluding:
	"""Tests for HTMLSerializer.serialize_excluding method."""

	def test_excludes_specified_nodes(self):
		"""Should exclude nodes by their id."""
		serializer = HTMLSerializer()

		popup = create_mock_node(node_name='dialog', attributes={'open': ''})
		text_node = create_mock_node(node_type=NodeType.TEXT_NODE, node_name='#text')
		text_node.node_value = 'Hello World'
		regular = create_mock_node(node_name='div', attributes={}, children=[text_node])

		root = create_mock_node(
			node_type=NodeType.DOCUMENT_NODE,
			node_name='#document',
			children=[regular, popup],
		)

		# Exclude the popup
		exclude_ids = {id(popup)}
		html = serializer.serialize_excluding(root, exclude_ids)

		assert '<dialog' not in html
		assert 'Hello World' in html

	def test_serialize_excluding_empty_set(self):
		"""Should serialize everything when exclude set is empty."""
		serializer = HTMLSerializer()

		text_node = create_mock_node(node_type=NodeType.TEXT_NODE, node_name='#text')
		text_node.node_value = 'Content'
		div = create_mock_node(node_name='div', attributes={}, children=[text_node])

		root = create_mock_node(
			node_type=NodeType.DOCUMENT_NODE,
			node_name='#document',
			children=[div],
		)

		html = serializer.serialize_excluding(root, set())
		assert 'Content' in html


class TestPopupRolesConstant:
	"""Tests for _POPUP_ROLES constant."""

	def test_popup_roles_contains_dialog(self):
		"""_POPUP_ROLES should contain 'dialog'."""
		assert 'dialog' in HTMLSerializer._POPUP_ROLES

	def test_popup_roles_contains_alertdialog(self):
		"""_POPUP_ROLES should contain 'alertdialog'."""
		assert 'alertdialog' in HTMLSerializer._POPUP_ROLES

	def test_popup_roles_is_frozenset(self):
		"""_POPUP_ROLES should be a frozenset for immutability."""
		assert isinstance(HTMLSerializer._POPUP_ROLES, frozenset)
