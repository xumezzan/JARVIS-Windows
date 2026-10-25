"""A bounded AST interpreter, not exec with a blacklist or a general Python sandbox."""

import ast
import csv
import io
import json
import math
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jarvis.tools.base import ToolError

MAX_ITEMS = 2000
MAX_VALUE = 16000
ALLOWED = (
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AugAssign,
    ast.For,
    ast.If,
    ast.Import,
    ast.alias,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Subscript,
    ast.Slice,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.keyword,
    ast.Attribute,
    ast.ListComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)


def validate_code(code: str) -> ast.Module:
    try:
        if not 0 < len(code) <= 8000:
            raise ValueError
        tree = ast.parse(code)
        nodes = list(ast.walk(tree))
        if len(nodes) > 500:
            raise ValueError
        for node in nodes:
            if not isinstance(node, ALLOWED):
                raise ValueError
            if isinstance(node, ast.Name) and node.id.startswith("_"):
                raise ValueError
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise ValueError
            if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
                raise ValueError
            if isinstance(node, ast.comprehension) and node.is_async:
                raise ValueError
            if isinstance(node, ast.Import) and any(
                item.name not in {"json", "csv", "math"} or (item.asname or "").startswith("_")
                for item in node.names
            ):
                raise ValueError

        def depth(node: ast.AST, level: int = 0) -> None:
            if level > 35:
                raise ValueError
            for child in ast.iter_child_nodes(node):
                depth(child, level + 1)

        depth(tree)
        return tree
    except (SyntaxError, ValueError, RecursionError):
        raise ToolError("computation_rejected") from None


def plain(value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
    budget = [20000] if budget is None else budget
    budget[0] -= 1
    if depth > 30 or budget[0] < 0:
        raise ToolError("computation_limit")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() > 4096:
            raise ToolError("computation_limit")
    elif type(value) is float:
        if not math.isfinite(value):
            raise ToolError("computation_limit")
    elif type(value) is str:
        budget[0] -= len(value)
        if len(value) > MAX_VALUE:
            raise ToolError("computation_limit")
        if budget[0] < 0:
            raise ToolError("computation_limit")
    elif type(value) in (list, tuple, dict):
        if len(value) > MAX_ITEMS:
            raise ToolError("computation_limit")
        for key in value:
            plain(key, depth + 1, budget)
            if type(value) is dict:
                if type(key) not in (str, int, float, bool) and key is not None:
                    raise ToolError("computation_rejected")
                plain(value[key], depth + 1, budget)
    else:
        raise ToolError("computation_rejected")
    return value


@dataclass(frozen=True)
class Function:
    call: Callable[..., Any]


@dataclass(frozen=True)
class Module:
    members: dict[str, Function]


class Interpreter:
    def __init__(self, data: str) -> None:
        self.output = ""
        self.steps = 0
        self.env: dict[str, Any] = {"data": data}
        functions: dict[str, Callable[..., Any]] = {
            "len": len,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "sorted": sorted,
            "list": list,
            "dict": dict,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "enumerate": lambda value: list(enumerate(value)),
            "zip": lambda *values: list(zip(*values, strict=True)),
            "range": self.range,
            "print": self.print,
        }
        self.env.update({name: Function(function) for name, function in functions.items()})
        self.modules = {
            "json": Module({"loads": Function(json.loads), "dumps": Function(self.json_dump)}),
            "csv": Module({"parse": Function(self.csv_parse), "format": Function(self.csv_format)}),
            "math": Module(
                {
                    name: Function(function)
                    for name, function in {
                        "sqrt": math.sqrt,
                        "floor": math.floor,
                        "ceil": math.ceil,
                        "isfinite": math.isfinite,
                    }.items()
                }
            ),
        }
        self.env.update(self.modules)

    def tick(self) -> None:
        self.steps += 1
        if self.steps > 20000:
            raise ToolError("computation_limit")

    @staticmethod
    def range(*args: int) -> list[int]:
        value = range(*args)
        if len(value) > MAX_ITEMS:
            raise ToolError("computation_limit")
        return list(value)

    def print(self, *args: Any, sep: str = " ", end: str = "\n") -> None:
        output = self.output + sep.join(str(arg) for arg in args) + end
        if len(output) > 4000:
            raise ToolError("computation_limit")
        self.output = output

    @staticmethod
    def json_dump(
        value: Any, *, ensure_ascii: bool = True, indent: int | None = None, sort_keys: bool = False
    ) -> str:
        if indent is not None and (type(indent) is not int or not 0 <= indent <= 4):
            raise ToolError("computation_limit")
        return json.dumps(value, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys)

    @staticmethod
    def csv_parse(text: str, delimiter: str = ",") -> list[dict[str, str | list[str] | None]]:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows: list[dict[str, str | list[str] | None]] = []
        for row in reader:
            if len(rows) >= MAX_ITEMS or len(row) > 100:
                raise ToolError("computation_limit")
            rows.append(row)
        return rows

    @staticmethod
    def csv_format(rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> str:
        stream = io.StringIO()
        names = fieldnames or (list(rows[0]) if rows else [])
        writer = csv.DictWriter(stream, fieldnames=names, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            if stream.tell() > MAX_VALUE:
                raise ToolError("computation_limit")
        return stream.getvalue()

    def assign(self, node: ast.expr, value: Any) -> None:
        plain(value)
        if isinstance(node, ast.Name) and node.id not in self.modules:
            self.env[node.id] = value
        elif isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == len(value):
            for target, item in zip(node.elts, value, strict=True):
                self.assign(target, item)
        else:
            raise ToolError("computation_rejected")

    def statement(self, node: ast.stmt) -> Any:
        self.tick()
        if isinstance(node, ast.Expr):
            return self.expression(node.value)
        if isinstance(node, ast.Import):
            for item in node.names:
                self.env[item.asname or item.name] = self.modules[item.name]
        elif isinstance(node, ast.Assign):
            value = self.expression(node.value)
            for target in node.targets:
                self.assign(target, value)
        elif isinstance(node, ast.AugAssign):
            if not isinstance(node.target, ast.Name):
                raise ToolError("computation_rejected")
            self.assign(
                node.target,
                self.binary(node.op, self.env[node.target.id], self.expression(node.value)),
            )
        elif isinstance(node, ast.If):
            for statement in node.body if self.expression(node.test) else node.orelse:
                self.statement(statement)
        elif isinstance(node, ast.For):
            for value in self.iterable(self.expression(node.iter)):
                self.tick()
                self.assign(node.target, value)
                for statement in node.body:
                    self.statement(statement)
            for statement in node.orelse:
                self.statement(statement)
        else:
            raise ToolError("computation_rejected")
        return None

    @staticmethod
    def iterable(value: Any) -> Any:
        plain(value)
        if not isinstance(value, (str, list, tuple, dict)) or len(value) > MAX_ITEMS:
            raise ToolError("computation_limit")
        return value

    def binary(self, op: ast.operator, left: Any, right: Any) -> Any:
        plain(left), plain(right)
        if isinstance(op, ast.Pow) and (not isinstance(right, (int, float)) or abs(right) > 100):
            raise ToolError("computation_limit")
        if isinstance(op, ast.Mult):
            for sequence, count in ((left, right), (right, left)):
                if isinstance(sequence, (str, list, tuple)) and isinstance(count, int):
                    maximum = MAX_VALUE if isinstance(sequence, str) else MAX_ITEMS
                    if len(sequence) * max(0, count) > maximum:
                        raise ToolError("computation_limit")
        # Percent-formatting can allocate unbounded width before checking the result.
        if isinstance(op, ast.Mod) and isinstance(left, str):
            raise ToolError("computation_rejected")
        functions = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
        }
        return plain(functions[type(op)](left, right))

    def comprehension(self, node: Any) -> list[tuple[Any, Any]]:
        values: list[tuple[Any, Any]] = []

        def visit(index: int) -> None:
            self.tick()
            if index == len(node.generators):
                if len(values) >= MAX_ITEMS:
                    raise ToolError("computation_limit")
                if isinstance(node, ast.DictComp):
                    values.append((self.expression(node.key), self.expression(node.value)))
                else:
                    values.append((None, self.expression(node.elt)))
                return
            generator = node.generators[index]
            for item in self.iterable(self.expression(generator.iter)):
                self.tick()
                self.assign(generator.target, item)
                if all(self.expression(condition) for condition in generator.ifs):
                    visit(index + 1)

        visit(0)
        return values

    def expression(self, node: ast.expr) -> Any:
        self.tick()
        if isinstance(node, ast.Constant):
            return plain(node.value)
        if isinstance(node, ast.Name):
            return self.env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return plain([self.expression(item) for item in node.elts])
        if isinstance(node, ast.Dict):
            return plain(
                {
                    self.expression(key): self.expression(value)
                    for key, value in zip(node.keys, node.values, strict=True)
                    if key is not None
                }
            )
        if isinstance(node, ast.Subscript):
            value = plain(self.expression(node.value))
            index = (
                slice(
                    *(
                        self.expression(part) if part else None
                        for part in (node.slice.lower, node.slice.upper, node.slice.step)
                    )
                )
                if isinstance(node.slice, ast.Slice)
                else self.expression(node.slice)
            )
            return plain(value[index])
        if isinstance(node, ast.BinOp):
            return self.binary(node.op, self.expression(node.left), self.expression(node.right))
        if isinstance(node, ast.UnaryOp):
            value = plain(self.expression(node.operand))
            if isinstance(node.op, ast.Not):
                return not value
            return plain(-value if isinstance(node.op, ast.USub) else +value)
        if isinstance(node, ast.BoolOp):
            result = self.expression(node.values[0])
            for value in node.values[1:]:
                if (isinstance(node.op, ast.And) and not result) or (
                    isinstance(node.op, ast.Or) and result
                ):
                    break
                result = self.expression(value)
            return result
        if isinstance(node, ast.Compare):
            left = plain(self.expression(node.left))
            functions = {
                ast.Eq: operator.eq,
                ast.NotEq: operator.ne,
                ast.Lt: operator.lt,
                ast.LtE: operator.le,
                ast.Gt: operator.gt,
                ast.GtE: operator.ge,
                ast.In: lambda a, b: a in b,
                ast.NotIn: lambda a, b: a not in b,
                ast.Is: operator.is_,
                ast.IsNot: operator.is_not,
            }
            for op, item in zip(node.ops, node.comparators, strict=True):
                right = plain(self.expression(item))
                if not functions[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self.expression(node.body if self.expression(node.test) else node.orelse)
        if isinstance(node, (ast.ListComp, ast.DictComp, ast.GeneratorExp)):
            values = self.comprehension(node)
            return plain(
                dict(values) if isinstance(node, ast.DictComp) else [value for _, value in values]
            )
        if isinstance(node, ast.Attribute):
            value = self.expression(node.value)
            if isinstance(value, Module):
                return value.members[node.attr]
            plain(value)
            allowed = (
                {
                    "strip",
                    "lstrip",
                    "rstrip",
                    "split",
                    "splitlines",
                    "join",
                    "replace",
                    "lower",
                    "upper",
                    "startswith",
                    "endswith",
                }
                if isinstance(value, str)
                else {"get", "keys", "values", "items"}
                if isinstance(value, dict)
                else set()
            )
            if node.attr not in allowed:
                raise ToolError("computation_rejected")
            # Only named, primitive methods. No dynamic attribute or object introspection.
            method = getattr(value, node.attr)
            return Function(
                (lambda: list(method())) if node.attr in {"keys", "values", "items"} else method
            )
        if isinstance(node, ast.Call):
            function = self.expression(node.func)
            if not isinstance(function, Function) or any(
                item.arg is None for item in node.keywords
            ):
                raise ToolError("computation_rejected")
            args = [plain(self.expression(item)) for item in node.args]
            kwargs = {
                item.arg: plain(self.expression(item.value))
                for item in node.keywords
                if item.arg is not None
            }
            # Expanding primitive operations remain under worker memory and wall limits.
            return plain(function.call(*args, **kwargs))
        if isinstance(node, ast.JoinedStr):
            return plain("".join(str(self.expression(item)) for item in node.values))
        if isinstance(node, ast.FormattedValue):
            value = plain(self.expression(node.value))
            if node.conversion == 114:
                value = repr(value)
            elif node.conversion in (115, 97):
                value = str(value)
            spec = self.expression(node.format_spec) if node.format_spec else ""
            if not re.fullmatch(r"[<^>]?\d{0,2}(?:\.\d{1,2})?[df%]?", spec):
                raise ToolError("computation_rejected")
            return plain(format(value, spec))
        raise ToolError("computation_rejected")


def evaluate(code: str, data: str) -> tuple[str, str]:
    try:
        interpreter = Interpreter(data)
        result = None
        for statement in validate_code(code).body:
            result = interpreter.statement(statement)
        if result is None:
            result = interpreter.env.get("result")
        encoded = json.dumps(plain(result), ensure_ascii=False, allow_nan=False)
        if len(encoded) > MAX_VALUE:
            raise ToolError("computation_limit")
        return encoded, interpreter.output
    except ToolError:
        raise
    except (MemoryError, RecursionError, OverflowError):
        raise ToolError("computation_limit") from None
    except Exception:
        raise ToolError("computation_rejected") from None
