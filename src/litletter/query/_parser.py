"""Lexer and recursive-descent parser for Litletter queries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from litletter.errors import QuerySyntaxError
from litletter.query._ast import And, Expression, Field, Not, Or, Query, Term


class _TokenKind(Enum):
    WORD = auto()
    PHRASE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    COLON = auto()
    EOF = auto()


@dataclass(frozen=True, slots=True)
class _Token:
    kind: _TokenKind
    value: str
    position: int


_OPERATORS = {
    "AND": _TokenKind.AND,
    "OR": _TokenKind.OR,
    "NOT": _TokenKind.NOT,
}


def parse_query(text: str) -> Query:
    """Parse ``text`` into a reusable query.

    Operators are case-insensitive. Their precedence, from highest to lowest,
    is ``NOT``, ``AND``, then ``OR``.
    """
    tokens = _Lexer(text).tokenize()
    return _Parser(text, tokens).parse()


class _Lexer:
    def __init__(self, text: str) -> None:
        self._text = text
        self._position = 0

    def tokenize(self) -> list[_Token]:
        tokens: list[_Token] = []
        while self._position < len(self._text):
            character = self._text[self._position]
            if character.isspace():
                self._position += 1
            elif character == "(":
                tokens.append(self._single_character(_TokenKind.LEFT_PAREN))
            elif character == ")":
                tokens.append(self._single_character(_TokenKind.RIGHT_PAREN))
            elif character == ":":
                tokens.append(self._single_character(_TokenKind.COLON))
            elif character in {'"', "'"}:
                tokens.append(self._phrase(character))
            else:
                tokens.append(self._word())
        tokens.append(_Token(_TokenKind.EOF, "", len(self._text)))
        return tokens

    def _single_character(self, kind: _TokenKind) -> _Token:
        position = self._position
        value = self._text[position]
        self._position += 1
        return _Token(kind, value, position)

    def _phrase(self, delimiter: str) -> _Token:
        start = self._position
        self._position += 1
        characters: list[str] = []
        while self._position < len(self._text):
            character = self._text[self._position]
            if character == delimiter:
                self._position += 1
                value = " ".join("".join(characters).split())
                if not value:
                    raise QuerySyntaxError(
                        "quoted phrase must not be empty", self._text, start
                    )
                return _Token(_TokenKind.PHRASE, value, start)
            if character == "\\":
                escape_position = self._position
                self._position += 1
                if self._position >= len(self._text):
                    raise QuerySyntaxError(
                        "unfinished escape in quoted phrase",
                        self._text,
                        escape_position,
                    )
                escaped = self._text[self._position]
                if escaped not in {delimiter, "\\"}:
                    raise QuerySyntaxError(
                        f"unsupported escape '\\{escaped}'",
                        self._text,
                        escape_position,
                    )
                characters.append(escaped)
                self._position += 1
                continue
            characters.append(character)
            self._position += 1
        raise QuerySyntaxError("unterminated quoted phrase", self._text, start)

    def _word(self) -> _Token:
        start = self._position
        while self._position < len(self._text):
            character = self._text[self._position]
            if character.isspace() or character in '():"':
                break
            self._position += 1
        value = self._text[start : self._position]
        if not value:
            raise QuerySyntaxError("unexpected character", self._text, start)
        kind = _OPERATORS.get(value.upper(), _TokenKind.WORD)
        return _Token(kind, value, start)


class _Parser:
    def __init__(self, text: str, tokens: list[_Token]) -> None:
        self._text = text
        self._tokens = tokens
        self._position = 0

    def parse(self) -> Query:
        if self._current.kind is _TokenKind.EOF:
            raise QuerySyntaxError("query must not be empty", self._text, 0)
        root = self._parse_or(Field.TITLE_ABSTRACT)
        if self._current.kind is not _TokenKind.EOF:
            if self._current.kind in {
                _TokenKind.WORD,
                _TokenKind.PHRASE,
                _TokenKind.LEFT_PAREN,
                _TokenKind.NOT,
            }:
                message = "expected AND or OR between expressions"
            else:
                message = f"unexpected token {self._describe(self._current)}"
            raise QuerySyntaxError(message, self._text, self._current.position)
        return Query(text=self._text, root=root)

    def _parse_or(self, field: Field) -> Expression:
        expression = self._parse_and(field)
        while self._match(_TokenKind.OR):
            expression = Or(expression, self._parse_and(field))
        return expression

    def _parse_and(self, field: Field) -> Expression:
        expression = self._parse_not(field)
        while self._match(_TokenKind.AND):
            expression = And(expression, self._parse_not(field))
        return expression

    def _parse_not(self, field: Field) -> Expression:
        if self._match(_TokenKind.NOT):
            return Not(self._parse_not(field))
        return self._parse_primary(field)

    def _parse_primary(self, field: Field) -> Expression:
        if (
            self._current.kind is _TokenKind.WORD
            and self._peek.kind is _TokenKind.COLON
        ):
            field_token = self._advance()
            self._advance()
            try:
                scoped_field = Field(field_token.value.lower())
            except ValueError as exc:
                supported = ", ".join(value.value for value in Field)
                raise QuerySyntaxError(
                    f"unknown field '{field_token.value}'; expected one of {supported}",
                    self._text,
                    field_token.position,
                ) from exc
            return self._parse_primary(scoped_field)

        if self._match(_TokenKind.LEFT_PAREN):
            opening = self._previous
            expression = self._parse_or(field)
            if not self._match(_TokenKind.RIGHT_PAREN):
                raise QuerySyntaxError(
                    "expected ')' to close group",
                    self._text,
                    opening.position,
                )
            return expression

        if self._current.kind in {_TokenKind.WORD, _TokenKind.PHRASE}:
            token = self._advance()
            return Term(
                text=token.value,
                field=field,
                phrase=token.kind is _TokenKind.PHRASE,
            )

        raise QuerySyntaxError(
            "expected a term, quoted phrase, or '('",
            self._text,
            self._current.position,
        )

    @property
    def _current(self) -> _Token:
        return self._tokens[self._position]

    @property
    def _peek(self) -> _Token:
        return self._tokens[min(self._position + 1, len(self._tokens) - 1)]

    @property
    def _previous(self) -> _Token:
        return self._tokens[self._position - 1]

    def _advance(self) -> _Token:
        token = self._current
        if token.kind is not _TokenKind.EOF:
            self._position += 1
        return token

    def _match(self, kind: _TokenKind) -> bool:
        if self._current.kind is not kind:
            return False
        self._advance()
        return True

    @staticmethod
    def _describe(token: _Token) -> str:
        if token.kind is _TokenKind.EOF:
            return "end of query"
        return repr(token.value)
