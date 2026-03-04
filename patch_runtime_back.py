import re

with open("app/runtime.py", "r", encoding="utf-8") as f:
    content = f.read()

# Modify _resolve_back_state to fallback to start_state
new_back_state = """    def _resolve_back_state(self, session: UserSession, action_text: str) -> str | None:
        \"\"\"Try to find an explicit 'Back' edge, otherwise pop history.\"\"\"
        explicit = self.catalog.resolve_action(session.state_id, action_text)
        if explicit:
            session.push_state(explicit)
            return explicit

        # Pop from history
        prev_state = session.pop_state()
        if prev_state is None:
            # If stack is empty or has only 1 element left, send user to start state
            start_sid = self.catalog.start_state_id
            if session.state_id != start_sid:
                session.jump_to_state(start_sid, reset_history=True)
                return start_sid
            return None # Already at start state, do nothing

        return prev_state"""

# Find the start and end of _resolve_back_state
content = re.sub(
    r"    def _resolve_back_state\(self, session: UserSession, action_text: str\) -> str \| None:\n.*?(?=\n    def )",
    new_back_state + "\n",
    content,
    flags=re.DOTALL
)

with open("app/runtime.py", "w", encoding="utf-8") as f:
    f.write(content)
