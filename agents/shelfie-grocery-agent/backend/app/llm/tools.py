import ast
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


def _evaluate_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate_ast(node.body)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.BinOp):
        left = _evaluate_ast(node.left)
        right = _evaluate_ast(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left**right

    if isinstance(node, ast.UnaryOp):
        value = _evaluate_ast(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.USub):
            return -value

    raise ValueError("Expression contains unsupported syntax.")


@tool
def calculate(expression: str) -> str:
    """Evaluate a simple arithmetic expression."""
    try:
        parsed = ast.parse(expression, mode="eval")
        result = _evaluate_ast(parsed)
    except Exception as exc:
        return f"Could not calculate `{expression}`: {exc}"

    if result.is_integer():
        return str(int(result))
    return str(result)


@tool
def get_current_datetime(timezone: str = "UTC") -> str:
    """Return current date-time in a timezone, for example UTC or Asia/Calcutta."""
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        now = datetime.now(ZoneInfo("UTC"))
        timezone = "UTC"
    return now.strftime(f"%Y-%m-%d %H:%M:%S ({timezone})")


AGENT_TOOLS = [calculate, get_current_datetime]

