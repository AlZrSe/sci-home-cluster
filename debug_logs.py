from fastapi.testclient import TestClient
from backend.main import app
from backend.store.memory import get_store
import asyncio

client = TestClient(app)
store = get_store()

# Check log history for job-1050
async def check_logs():
    job = await store.get_job('job-1050')
    print(f'Job: {job.job_id}, status: {job.status}')
    logs = await store.get_job_logs('job-1050')
    print(f'Logs count: {len(logs)}')
    if logs:
        print(f'First log: {logs[0]}')
    else:
        print('No logs found')
        
    # Check log history directly
    print(f'Log history keys: {list(store._log_history.keys())[:5]}')
    if 'job-1050' in store._log_history:
        print(f'job-1050 log history: {store._log_history["job-1050"][:3]}')

asyncio.run(check_logs())