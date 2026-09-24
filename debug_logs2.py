from backend.store.memory import get_store
import asyncio

store = get_store()

# Check log history for job-1050
async def check_logs():
    job = await store.get_job('job-1050')
    print(f'Job: {job.job_id}, status: {job.status}')
    
    # Call get_job_logs which should generate logs if empty
    logs = await store.get_job_logs('job-1050')
    print(f'After get_job_logs - Logs count: {len(logs)}')
    if logs:
        print(f'First log: {logs[0]}')
    else:
        print('Still no logs')
        
    # Check log history directly
    if 'job-1050' in store._log_history:
        print(f'job-1050 log history after: {store._log_history["job-1050"][:3]}')

asyncio.run(check_logs())