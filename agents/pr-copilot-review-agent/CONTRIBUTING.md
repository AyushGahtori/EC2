# CONTRIBUTING.md — Repository Guidelines
# This file is loaded at runtime by PR Copilot as the review guidelines.

## Code Style
- Follow PEP-8 strictly (max line length 99 chars)
- Use type hints on all public functions and methods
- All modules must have a module-level docstring
- Use f-strings, not `.format()` or `%` formatting

## Naming Conventions
- snake_case for variables, functions, modules
- PascalCase for classes
- UPPER_SNAKE_CASE for constants
- Prefix private helpers with a single underscore `_`

## Documentation
- Every public function/method must have a Google-style docstring
- Include `Args:`, `Returns:`, and `Raises:` sections where applicable
- Avoid obvious comments — prefer self-documenting code

## Error Handling
- Never use bare `except:` — always catch specific exception types
- Log exceptions with `logger.exception()` to capture tracebacks
- Propagate errors upward via return values or custom exceptions, not sys.exit()

## Security
- Never hardcode secrets, passwords, or API keys
- Always use environment variables or a secrets manager
- Validate all external inputs before processing

## Testing
- Every public node function must have at least one unit test
- Use `pytest` — no unittest.TestCase classes
- Mock external calls (GitHub API, Ollama) — tests must run offline

## Architecture
- Keep nodes stateless — nodes read from state, return new state
- Do not import from sibling nodes — depend only on state and config
- Use Pydantic models for all structured data, not raw dicts
