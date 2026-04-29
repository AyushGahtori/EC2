# Shelfie Grocery Agent - Validation Use Cases

1. Simple prompt  
- Action: `run_shelfie_grocery_agent`  
- Prompt: `Plan a weekly grocery list for 2 adults with high-protein meals.`  
- Expect: `success`, `type=shelfie_conversation_result`, non-empty `responseText`.

2. Multi-step continuation  
- First prompt: `Create a vegetarian grocery list for 5 dinners.`  
- Second prompt (same session): `Now optimize it for a tighter budget and keep protein high.`  
- Expect: second response uses prior context and same session.

3. Failure path (unsupported action)  
- Action: `unsupported_action_foo`  
- Expect: `failed` with `supportedActions` guidance.

4. Auth-related scenario  
- Action: `run_shelfie_grocery_agent` with no `userId`  
- Expect: graceful fallback to `anonymous` user without auth errors.

5. Caching-heavy behavior  
- Repeat same prompt in same session quickly.  
- Expect: fast response and persisted session history with Redis or memory fallback.

6. Deep traversal  
- Prompt: `Build a 14-day grocery strategy with pantry staples, fresh produce rotation, and low-waste substitutions.`  
- Expect: structured long-form response without endpoint failure.

7. Edge case prompt  
- Prompt: `I need gluten-free, lactose-free, low-sodium, Indian-friendly weekly groceries.`  
- Expect: constrained list reasoning and no crash.

8. Empty input  
- Action: `run_shelfie_grocery_agent` with empty `prompt/message/query`  
- Expect: `needs_input` with `suggestedInputs`.

9. Partial data  
- Action: `get_history` with missing `session_id`  
- Expect: `needs_input` asking for `session_id or chatId`.

10. Retry/recovery  
- Force transient model failure (network/provider hiccup), then retry same action.  
- Expect: internal retry attempt and clear `failed` response without raw stack trace if retry still fails.
