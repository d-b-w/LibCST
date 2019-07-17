# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Iterable,
    Iterator,
    List,
    Optional,
    Pattern,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from libcst._add_slots import add_slots
from libcst._maybe_sentinel import MaybeSentinel
from libcst._removal_sentinel import RemovalSentinel


if TYPE_CHECKING:
    # These are circular dependencies only used for typing purposes
    from libcst.nodes._base import CSTNode
    from libcst.visitors import CSTVisitorT
    from libcst.metadata.position_provider import (
        BasicPositionProvider,
        SyntacticPositionProvider,
    )


_CSTNodeT = TypeVar("_CSTNodeT", bound="CSTNode")
_ProviderT = Union[Type["BasicPositionProvider"], Type["SyntacticPositionProvider"]]
_CodePositionT = Union[Tuple[int, int], "CodePosition"]


NEWLINE_RE: Pattern[str] = re.compile(r"\r\n?|\n")


@add_slots
@dataclass(frozen=True)
class CodePosition:
    line: int
    column: int


@add_slots
@dataclass(frozen=True)
class CodeRange:
    start: CodePosition
    end: CodePosition

    @classmethod
    def create(cls, start: Tuple[int, int], end: Tuple[int, int]) -> "CodeRange":
        return CodeRange(CodePosition(start[0], start[1]), CodePosition(end[0], end[1]))


@add_slots
@dataclass(frozen=False)
class CodegenState:
    # These are derived from a Module
    default_indent: str
    default_newline: str

    provider: _ProviderT = field(init=False)

    indent_tokens: List[str] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)

    line: int = 1  # one-indexed
    column: int = 0  # zero-indexed

    def __post_init__(self) -> None:
        from libcst.metadata.position_provider import BasicPositionProvider

        self.provider = BasicPositionProvider

    def increase_indent(self, value: str) -> None:
        self.indent_tokens.append(value)

    def decrease_indent(self) -> None:
        self.indent_tokens.pop()

    def add_indent_tokens(self) -> None:
        self.tokens.extend(self.indent_tokens)
        for token in self.indent_tokens:
            self._update_position(token)

    def add_token(self, value: str) -> None:
        self.tokens.append(value)
        self._update_position(value)

    def _update_position(self, value: str) -> None:
        """
        Computes new line and column numbers from adding the token [value].
        """
        segments = NEWLINE_RE.split(value)
        if len(segments) == 1:  # contains no newlines
            # no change to self.lines
            self.column += len(value)
        else:
            self.line += len(segments) - 1
            # newline resets column back to 0, but a trailing token may shift column
            self.column = len(segments[-1])

    def record_position(self, node: _CSTNodeT, position: CodeRange) -> None:
        # Don't overwrite existing position information
        # (i.e. semantic position has already been recorded)
        if self.provider not in node._metadata:
            node._metadata[self.provider] = position

    @contextmanager
    def record_syntactic_position(self, node: _CSTNodeT) -> Iterator[None]:
        yield


class SyntacticCodegenState(CodegenState):
    """
    Pass to codegen to record the syntatic position of nodes.
    """

    def __post_init__(self) -> None:
        from libcst.metadata.position_provider import SyntacticPositionProvider

        self.provider = SyntacticPositionProvider

    @contextmanager
    def record_syntactic_position(self, node: _CSTNodeT) -> Iterator[None]:
        start = CodePosition(self.line, self.column)
        try:
            yield
        finally:
            end = CodePosition(self.line, self.column)
            node._metadata[self.provider] = CodeRange(start, end)


def visit_required(
    fieldname: str, node: _CSTNodeT, visitor: "CSTVisitorT"
) -> _CSTNodeT:
    """
    Given a node, visits the node using `visitor`. If removal is attempted by the
    visitor, an exception is raised.
    """
    result = node.visit(visitor)
    if isinstance(result, RemovalSentinel):
        raise TypeError(
            f"We got a RemovalSentinel while visiting a {type(node).__name__}. This "
            + "node's parent does not allow it to be removed."
        )
    return result


def visit_optional(
    fieldname: str, node: Optional[_CSTNodeT], visitor: "CSTVisitorT"
) -> Optional[_CSTNodeT]:
    """
    Given an optional node, visits the node if it exists with `visitor`. If the node is
    removed, returns None.
    """
    if node is None:
        return None
    result = node.visit(visitor)
    return None if isinstance(result, RemovalSentinel) else result


def visit_sentinel(
    fieldname: str, node: Union[_CSTNodeT, MaybeSentinel], visitor: "CSTVisitorT"
) -> Union[_CSTNodeT, MaybeSentinel]:
    """
    Given a node that can be a real value or a sentinel value, visits the node if it
    is real with `visitor`. If the node is removed, returns MaybeSentinel.
    """
    if isinstance(node, MaybeSentinel):
        return MaybeSentinel.DEFAULT
    result = node.visit(visitor)
    return MaybeSentinel.DEFAULT if isinstance(result, RemovalSentinel) else result


def visit_iterable(
    fieldname: str, children: Iterable[_CSTNodeT], visitor: "CSTVisitorT"
) -> Iterable[_CSTNodeT]:
    """
    Given an iterable of children, visits each child with `visitor`, and yields the new
    children with any `RemovalSentinel` values removed.
    """
    for child in children:
        new_child = child.visit(visitor)
        if not isinstance(new_child, RemovalSentinel):
            yield new_child


def visit_sequence(
    fieldname: str, children: Sequence[_CSTNodeT], visitor: "CSTVisitorT"
) -> Sequence[_CSTNodeT]:
    """
    A convenience wrapper for `visit_iterable` that returns a sequence instead of an
    iterable.
    """
    return tuple(visit_iterable(fieldname, children, visitor))
