from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

# Test WebSocket connection
print("=== WebSocket test ===")
with client.websocket_connect("/api/v1/jobs/job-1050/logs/stream") as ws:
    print("Connected!")
    # Receive a few messages
    for i in range(5):
        try:
            msg = ws.receive_text()
            print(
                f"Message {i+1}: {msg[:100]}..."
                if len(msg) > 100
                else f"Message {i+1}: {msg}"
            )
        except Exception as e:
            print(f"Error receiving: {e}")
            break
