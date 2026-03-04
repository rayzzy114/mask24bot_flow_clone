from app.sessions import UserSession

def test_empty_stack():
    s = UserSession(state_id="start")
    print("Initial history:", s.history) # Oh, history defaults to empty list.
    print("pop:", s.pop_state()) # This will hit len(self.history) <= 1, return None
    print("history after pop:", s.history)

test_empty_stack()
