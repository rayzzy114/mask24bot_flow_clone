import json
from collections import Counter

def main():
    events_file = "data/raw/events.json"
    with open(events_file, "r", encoding="utf-8") as f:
        events = json.load(f)

    start_hits = Counter()
    for event in events:
        if isinstance(event, dict) and event.get("from_action") == "/start":
            state_id = str(event.get("state_id", ""))
            if state_id:
                start_hits[state_id] += 1

    print("Start state candidates:")
    for state_id, count in start_hits.most_common():
        print(f"{state_id}: {count}")

if __name__ == "__main__":
    main()
