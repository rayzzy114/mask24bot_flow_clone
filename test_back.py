from app.sessions import UserSession

def test_session_history():
    s = UserSession(state_id="start")
    print(s.history)  # Expect empty list initially due to default factory if not provided, but we pass state_id

    s = UserSession(state_id="start", history=["start"])
    print(s.history)
    s.push_state("A")
    print(s.history)
    s.push_state("B")
    print(s.history)
    print("pop:", s.pop_state())
    print(s.history)
    print("pop:", s.pop_state())
    print(s.history)
    print("pop:", s.pop_state())  # Expect None
    print(s.history)

test_session_history()
