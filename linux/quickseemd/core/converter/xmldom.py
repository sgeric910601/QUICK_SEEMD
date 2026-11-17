"""迷你 XML DOM（對應原版 CVXMLNode／CVXML），給 plist 與 XLSX 讀取用。

用 expat 解析；拒絕任何實體宣告（避免實體展開攻擊），也不會讀取外部 DTD。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.parsers import expat


class XMLRefused(ValueError):
    """XML 含有不允許的內容（實體宣告）。"""


class XMLTooLarge(ValueError):
    """XML 的元素數量超過上限（避免惡意檔案吃光記憶體）。"""


@dataclass(slots=True)
class Node:
    name: str
    attributes: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    text: str = ""

    @property
    def local_name(self) -> str:
        """去掉命名空間前綴的名稱。"""
        return self.name.rsplit(":", 1)[-1]

    def first_child(self, local: str) -> Node | None:
        for c in self.children:
            if c.local_name == local:
                return c
        return None

    def children_named(self, local: str) -> list[Node]:
        return [c for c in self.children if c.local_name == local]

    def attribute(self, local: str) -> str | None:
        if local in self.attributes:
            return self.attributes[local]
        for k, v in self.attributes.items():
            if k.endswith(":" + local):
                return v
        return None

    def descendants(self, local: str) -> list[Node]:
        """依文件順序找出所有指定名稱的子孫。"""
        out: list[Node] = []
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            if node.local_name == local:
                out.append(node)
            stack.extend(reversed(node.children))
        return out


def _refuse_entity(*_args: object) -> None:
    raise XMLRefused("XML 實體宣告不被允許")


def new_parser() -> expat.XMLParserType:
    """不展開實體、不讀外部 DTD 的 expat 解析器（有實體宣告就丟 XMLRefused）。"""
    parser = expat.ParserCreate()
    parser.EntityDeclHandler = _refuse_entity
    parser.UnparsedEntityDeclHandler = _refuse_entity
    parser.ExternalEntityRefHandler = _refuse_entity
    parser.buffer_text = True
    return parser


def parse(data: bytes, max_nodes: int | None = None) -> Node | None:
    """解析成功回傳 #document 節點；格式錯誤或含實體宣告時回傳 None。

    max_nodes：元素數量上限，超過時丟 XMLTooLarge。
    """
    root = Node("#document")
    stack = [root]
    count = [0]

    def start(name: str, attrs: dict[str, str]) -> None:
        count[0] += 1
        if max_nodes is not None and count[0] > max_nodes:
            raise XMLTooLarge("XML 元素太多")
        node = Node(name, dict(attrs))
        stack[-1].children.append(node)
        stack.append(node)

    def end(_name: str) -> None:
        if len(stack) > 1:
            stack.pop()

    def chars(text: str) -> None:
        stack[-1].text += text

    parser = new_parser()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, XMLRefused):
        return None
    return root


_ESCAPE_RE = re.compile('[&<>"\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')
_ESCAPE_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def escape(s: str) -> str:
    """XML 跳脫；XML 1.0 不允許的控制字元直接略過。"""
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP.get(m.group(0), ""), s)
