import json
from pathlib import Path

def main():
    raw_dir = Path("data/raw")
    flow_file = raw_dir / "flow.json"
    edges_file = raw_dir / "edges.json"

    with open(flow_file, "r", encoding="utf-8") as f:
        flow = json.load(f)

    with open(edges_file, "r", encoding="utf-8") as f:
        edges = json.load(f)

    # 1. Analyze nodes and edges
    states = set(str(k) for k in flow.keys() if isinstance(flow[k], dict))
    transitions = {src: set() for src in states}
    destinations = set()

    for edge in edges:
        src = str(edge.get("from", ""))
        action = str(edge.get("action", ""))
        dst = str(edge.get("to", ""))
        if src in transitions:
            transitions[src].add(action)
        destinations.add(dst)

    # 2. Find dead ends (states with no outgoing transitions, unless they are specific terminal states)
    dead_ends = []
    for state_id in states:
        if not transitions.get(state_id):
            # Check if this state has buttons that might imply a transition
            state_data = flow[state_id]
            buttons = state_data.get("button_rows", []) or state_data.get("buttons", [])
            has_buttons = len(buttons) > 0
            if has_buttons:
                dead_ends.append(state_id)

    print(f"Total states: {len(states)}")
    print(f"States with buttons but NO edges out: {len(dead_ends)}")
    if dead_ends:
        print("Sample dead ends:", dead_ends[:5])

    # 3. Find invalid destinations
    invalid_dst = destinations - states
    print(f"Edges pointing to non-existent states: {len(invalid_dst)}")
    if invalid_dst:
        print("Invalid destinations:", list(invalid_dst)[:5])

    # 4. Check for states unreachable from start (approximate, since we don't know start explicitly here without events, but we can assume states with no incoming edges that aren't the start)
    # We will just print the number of states with 0 incoming edges
    incoming_counts = {state: 0 for state in states}
    for edge in edges:
        dst = str(edge.get("to", ""))
        if dst in incoming_counts:
            incoming_counts[dst] += 1

    orphans = [state for state, count in incoming_counts.items() if count == 0]
    print(f"States with 0 incoming edges (potential orphans or start state): {len(orphans)}")
    if orphans:
        print("Sample orphans:", orphans[:5])

if __name__ == "__main__":
    main()
