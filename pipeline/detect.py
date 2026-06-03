from ultralytics import YOLO
import supervision as sv
import cv2
import json
from datetime import datetime

# Load YOLO model
model = YOLO("yolov8n.pt")

# Create ByteTrack tracker
tracker = sv.ByteTrack()

# Keep track of visitors already seen
seen_ids = set()

# Track current zone of each visitor
visitor_zones = {}

# Open video
cap = cv2.VideoCapture("pipeline/videos/sample.mp4")

while True:

    ret, frame = cap.read()

    if not ret:
        break

    frame_height, frame_width = frame.shape[:2]

    # Run YOLO
    result = model(frame)[0]

    # Convert YOLO detections
    detections = sv.Detections.from_ultralytics(result)

    # Keep only PERSON class
    person_mask = detections.class_id == 0
    detections = detections[person_mask]

    # Update tracker
    detections = tracker.update_with_detections(detections)

    if detections.tracker_id is not None:

        for i, tracker_id in enumerate(detections.tracker_id):

            x1, y1, x2, y2 = map(int, detections.xyxy[i])

            # Find center of person
            center_x = (x1 + x2) / 2

            # Determine zone
            if center_x < frame_width / 3:
                zone = "LEFT"

            elif center_x < (2 * frame_width / 3):
                zone = "CENTER"

            else:
                zone = "RIGHT"

            # Draw bounding box
            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                2
            )

            # Draw label
            cv2.putText(
                frame,
                f"ID:{tracker_id} | {zone}",
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2
            )

            # ENTRY event (only once)
            if tracker_id not in seen_ids:

                seen_ids.add(tracker_id)

                entry_event = {
                    "visitor_id": f"VIS_{tracker_id}",
                    "event_type": "ENTRY",
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }

                print(entry_event)

                with open("events.jsonl", "a") as f:
                    f.write(json.dumps(entry_event) + "\n")

            # ZONE_ENTER event
            previous_zone = visitor_zones.get(tracker_id)

            if previous_zone != zone:

                visitor_zones[tracker_id] = zone

                zone_event = {
                    "visitor_id": f"VIS_{tracker_id}",
                    "event_type": "ZONE_ENTER",
                    "zone_id": zone,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }

                print(zone_event)

                with open("events.jsonl", "a") as f:
                    f.write(json.dumps(zone_event) + "\n")

    # Show video
    cv2.imshow("Person Tracking", frame)

    # ESC key to stop
    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()

print("\nFinished processing video.")
print("Events saved to events.jsonl")