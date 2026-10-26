import re

with open('backend/tests/unit/test_websocket.py', 'r') as f:
    content = f.read()

# Replace False patches
content = re.sub(
    r'(\s+)with patch\.object\(settings, "LOCALHOST_BYPASS", False\):',
    r'\1from backend.core.config import LocalhostBypassMode\n\1with patch.object(settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.DISABLED):',
    content
)

# Replace True patches
content = re.sub(
    r'(\s+)with patch\.object\(settings, "LOCALHOST_BYPASS", True\):',
    r'\1from backend.core.config import LocalhostBypassMode\n\1with patch.object(settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.HOST_HEADER):',
    content
)

with open('backend/tests/unit/test_websocket.py', 'w') as f:
    f.write(content)
print('Done')