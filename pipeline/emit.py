import requests
import json

API_URL = "http://127.0.0.1:8000/events/ingest"

events = []

with open("events.jsonl", "r") as f:

    for idx, line in enumerate(f):

        data = json.loads(line)

        # Original event
        event = {
            "event_id": f"{data['visitor_id']}_{idx}",
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_01",
            "visitor_id": data["visitor_id"],
            "event_type": data["event_type"],
            "timestamp": data["timestamp"],
            "zone_id": data.get("zone_id"),
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.95,
            "metadata": {}
        }

        events.append(event)

        # Simulate queue + purchase events
        if (
            data["event_type"] == "ZONE_ENTER"
            and data.get("zone_id") == "CENTER"
            and idx % 10 == 0
        ):

            # Queue event
            queue_event = {
                "event_id": f"{data['visitor_id']}_queue_{idx}",
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_01",
                "visitor_id": data["visitor_id"],
                "event_type": "BILLING_QUEUE_JOIN",
                "timestamp": data["timestamp"],
                "zone_id": data.get("zone_id"),
                "dwell_ms": 0,
                "is_staff": False,
                "confidence": 0.95,
                "metadata": {}
            }

            events.append(queue_event)

            # Purchase event
            purchase_event = {
                "event_id": f"{data['visitor_id']}_purchase_{idx}",
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_01",
                "visitor_id": data["visitor_id"],
                "event_type": "PURCHASE",
                "timestamp": data["timestamp"],
                "zone_id": data.get("zone_id"),
                "dwell_ms": 0,
                "is_staff": False,
                "confidence": 0.95,
                "metadata": {}
            }

            events.append(purchase_event)

payload = {
    "events": events
}

response = requests.post(
    API_URL,
    json=payload
)

print("Status Code:", response.status_code)
print("Response:", response.json())
print("Total Events Sent:", len(events))