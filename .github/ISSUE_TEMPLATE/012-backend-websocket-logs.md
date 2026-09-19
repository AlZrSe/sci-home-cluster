# Backend WebSocket Log Streaming

## Description
Implement WebSocket endpoint for real-time job log streaming with proper subprotocol handling.

## User Stories
- As a user, I can stream job logs in real-time via WebSocket so that I can monitor job progress
- As a user, I receive connection status updates so that I know when the stream is active
- As a developer, I want WebSocket implementation that matches the mock server behavior

## Acceptance Criteria
- [ ] Create `backend/backend/websocket/logs.py` implementing WebSocket handler
- [ ] Implement GET /jobs/{job_id}/logs/stream endpoint
- [ ] Endpoint requires authentication (Bearer token)
- [ ] WebSocket accepts subprotocol for structured messages
- [ ] Messages follow format: { type: "log" | "status", payload: string }
- [ ] For type "log": payload is a log line string
- [ ] For type "status": payload is one of "connecting", "open", "closed"
- [ ] While job is RUNNING, emit log lines at appropriate intervals
- [ ] When job completes/fails/cancelled, send final logs then close with status "closed"
- [ ] Implement proper connection lifecycle:
  - Immediately send "connecting" status
  - After short delay, send "open" status and begin log streaming
  - On disconnect or job completion, send "closed" status
- [ ] Log streaming should mimic mock-server behavior:
  - Start with initial log lines (job accepted, syncing, environment ready, starting command)
  - Then stream generated log lines using same templates
  - Use same timing mechanisms as mock server
- [ ] Handle job not found (404) and authentication errors (401) before WebSocket acceptance
- [ ] Write unit tests for WebSocket connection and message flow

## Technical Notes
- Use FastAPI's WebSocket support
- Use websockets library for underlying implementation (FastAPI uses this)
- Implement as async generator or proper WebSocket endpoint
- Subprotocol negotiation should work correctly
- Heartbeat/ping mechanisms optional but can be implemented
- Ensure proper error handling and connection cleanup
- Test with WebSocket client libraries
- Follow mock-server timestamp format: YYYY-MM-DD HH:MM:SS
- Use same log templates and stamping mechanism as mock server
- Consider storing stream state per connection to avoid conflicts