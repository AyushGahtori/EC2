# Shelfie Grocery Agent - API Validation Use Cases

All scenarios target `POST /shelfie/action` unless otherwise specified.

1. Simple prompt
- Action: `run_shelfie_grocery_agent`
- Input: `{"prompt":"Plan a weekly grocery list for 2 adults with high-protein meals."}`
- Expected HTTP: `200`
- JSON assertions: `status in {"success","failed"}`, `type=="shelfie_conversation_result"` on success, `result.responseText` non-empty on success.

2. Multi-step continuation
- Action: `run_shelfie_grocery_agent` twice with same `session_id`
- Inputs:
`{"session_id":"case-2","prompt":"Create a vegetarian grocery list for 5 dinners."}`
`{"session_id":"case-2","prompt":"Now optimize it for a tighter budget and keep protein high."}`
- Expected HTTP: `200` for both calls
- JSON assertions: second response keeps `result.session_id=="case-2"` and returns `result.history` with prior turns.

3. Failure path (unsupported action)
- Action: `unsupported_action_foo`
- Input: `{"action":"unsupported_action_foo"}`
- Expected HTTP: `200`
- JSON assertions: `status=="failed"`, `error` starts with `unknown_action:`, `result.supportedActions` present.

4. Auth-related scenario
- Action: `run_shelfie_grocery_agent` with no `userId`
- Input: `{"prompt":"Plan groceries for 3 days."}`
- Expected HTTP: `200`
- JSON assertions: no auth error, `status in {"success","failed"}`, and when successful `result.user_id=="anonymous"`.

5. Caching-heavy behavior
- Action: `run_shelfie_grocery_agent` repeated quickly in same `session_id`
- Input: same prompt and session twice
- Expected HTTP: `200` for both
- JSON assertions: second call returns same `result.session_id`, `result.history` length increases, no server exception leakage.

6. Deep traversal
- Action: `run_shelfie_grocery_agent`
- Input: `{"prompt":"Build a 14-day grocery strategy with pantry staples, produce rotation, and low-waste substitutions."}`
- Expected HTTP: `200`
- JSON assertions: `status in {"success","failed"}`, and response shape remains valid (`type`, `message`, `summary` fields present).

7. Edge case prompt
- Action: `run_shelfie_grocery_agent`
- Input: `{"prompt":"I need gluten-free, lactose-free, low-sodium, Indian-friendly weekly groceries."}`
- Expected HTTP: `200`
- JSON assertions: structured response envelope is valid; no tracebacks/internal paths returned in `message`, `summary`, or `error`.

8. Empty input
- Action: `run_shelfie_grocery_agent`
- Input: empty payload for prompt/message/query
- Expected HTTP: `200`
- JSON assertions: `status=="needs_input"`, `error=="missing_prompt"`, `result.suggestedInputs` includes prompt fields.

9. Partial data
- Action: `get_history`
- Input: missing `session_id` and `chatId`
- Expected HTTP: `200`
- JSON assertions: `status=="needs_input"`, `error=="missing_session_id"`, `result.suggestedInputs` includes `session_id`.

10. Retry/recovery
- Action: `run_shelfie_grocery_agent` during temporary provider outage
- Input: valid prompt
- Expected HTTP: `200` (service-level handled failure envelope)
- JSON assertions: `status=="failed"`, `error` begins with `shelfie_grocery_failed:`, and no raw stack trace in response.

Health alias checks:
- `GET /shelfie/health` -> HTTP `200`, `status=="healthy"`
- `GET /shelfie-grocery/health` -> HTTP `200`, `status=="healthy"`
