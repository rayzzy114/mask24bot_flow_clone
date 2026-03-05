import json

def main():
    with open("data/raw/flow.json", "r", encoding="utf-8") as f:
        flow = json.load(f)
    with open("data/raw/edges.json", "r", encoding="utf-8") as f:
        edges = json.load(f)

    states = set(str(k) for k in flow.keys() if isinstance(flow[k], dict))
    graph = {state: set() for state in states}

    # We add transitions. Also include '<next-message>', '<input>', '<manual-input>' as valid
    for edge in edges:
        src = str(edge.get("from", ""))
        dst = str(edge.get("to", ""))
        if src in graph and dst in states:
            graph[src].add(dst)

    # We know 39c2aa6a1534fa73a1a2ab96eef4cbb4 is the primary start state.
    start_state = "39c2aa6a1534fa73a1a2ab96eef4cbb4"

    visited = set()
    queue = [start_state]

    # Simple BFS
    while queue:
        curr = queue.pop(0)
        if curr not in visited:
            visited.add(curr)
            queue.extend(list(graph.get(curr, [])))

    unreachable = states - visited
    print(f"Total states: {len(states)}")
    print(f"Reachable from {start_state}: {len(visited)}")
    print(f"Unreachable: {len(unreachable)}")

    if unreachable:
        print("Unreachable states:")
        for state in unreachable:
            text = flow.get(state, {}).get("text", "")[:50]
            print(f"- {state}: {text}")

if __name__ == "__main__":
    main()
