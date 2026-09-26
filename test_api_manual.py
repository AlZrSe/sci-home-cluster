from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

# Test GET /jobs
print("=== GET /jobs ===")
r = client.get("/api/v1/jobs/")
print(f"Status: {r.status_code}")
data = r.json()
print(f'Total: {data["total"]}')
print(f'Items count: {len(data["items"])}')
if data["items"]:
    print(f'First job: {data["items"][0]["job_id"]}')

# Test GET /jobs with status filter
print("\n=== GET /jobs?status=RUNNING ===")
r = client.get("/api/v1/jobs/?status=RUNNING")
print(f"Status: {r.status_code}")
data = r.json()
print(f'Total: {data["total"]}')
print(f'Items count: {len(data["items"])}')

# Test GET /jobs with search
print("\n=== GET /jobs?search=protein ===")
r = client.get("/api/v1/jobs/?search=protein")
print(f"Status: {r.status_code}")
data = r.json()
print(f'Total: {data["total"]}')
print(f'Items count: {len(data["items"])}')

# Test GET /jobs/{job_id}
print("\n=== GET /jobs/job-1050 ===")
r = client.get("/api/v1/jobs/job-1050")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    print(f'Job ID: {r.json()["job_id"]}')
    print(f'Status: {r.json()["status"]}')

# Test GET /jobs/{job_id}/metrics
print("\n=== GET /jobs/job-1050/metrics ===")
r = client.get("/api/v1/jobs/job-1050/metrics")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    m = r.json()
    print(f'Job ID: {m["job_id"]}')
    print(f'GPU metrics count: {len(m["gpu_metrics"])}')
    print(f'Summary: {m["summary"]}')

# Test GET /jobs/{job_id}/logs
print("\n=== GET /jobs/job-1050/logs ===")
r = client.get("/api/v1/jobs/job-1050/logs")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    logs = r.json()
    print(f"Log lines: {len(logs)}")
    if logs:
        print(f"First log: {logs[0][:80]}...")

# Test POST /jobs (create)
print("\n=== POST /jobs (create) ===")
yaml_content = """
name: test-create-job
command: python train.py
working_dir: /sync/projects/test
env:
  PYTHONUNBUFFERED: '1'
resources:
  gpus: 1
  cpus: 4
  memory_gb: 16
  vram_gb: 8
paths:
  input: data/test/in
  output: data/test/out
retry:
  max_retries: 3
  retry_delay_seconds: 60
"""
files = {"job.yaml": ("job.yaml", yaml_content, "text/yaml")}
r = client.post("/api/v1/jobs/", files=files)
print(f"Status: {r.status_code}")
if r.status_code == 201:
    job = r.json()
    print(f'Created job ID: {job["job_id"]}')
    print(f'Status: {job["status"]}')

# Test POST /jobs/{job_id}/retry (on failed job)
print("\n=== POST /jobs/job-1046/retry (FAILED job) ===")
r = client.post("/api/v1/jobs/job-1046/retry")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    job = r.json()
    print(f'Retried job ID: {job["job_id"]}')
    print(f'New status: {job["status"]}')
    print(f'Retry count: {job["retry_count"]}')

# Test POST /jobs/{job_id}/cancel (on running job)
print("\n=== POST /jobs/job-1050/cancel (RUNNING job) ===")
r = client.post("/api/v1/jobs/job-1050/cancel")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    job = r.json()
    print(f'Cancelled job ID: {job["job_id"]}')
    print(f'New status: {job["status"]}')

# Test DELETE /jobs/{job_id}
print("\n=== DELETE /jobs/job-1046 (FAILED job) ===")
r = client.delete("/api/v1/jobs/job-1046")
print(f"Status: {r.status_code}")

# Test error cases
print("\n=== Error cases ===")
# 404 - job not found
r = client.get("/api/v1/jobs/job-9999")
print(f"GET /jobs/job-9999: {r.status_code}")

# 409 - retry non-retryable job
r = client.post("/api/v1/jobs/job-1050/retry")  # RUNNING job
print(f"POST /jobs/job-1050/retry (RUNNING): {r.status_code}")

# 409 - cancel non-cancellable job
r = client.post("/api/v1/jobs/job-1041/cancel")  # COMPLETED job
print(f"POST /jobs/job-1041/cancel (COMPLETED): {r.status_code}")

# 400 - invalid YAML
files = {"job.yaml": ("job.yaml", "invalid: yaml: [", "text/yaml")}
r = client.post("/api/v1/jobs/", files=files)
print(f"POST /jobs (invalid YAML): {r.status_code}")

# Test pagination
print("\n=== Pagination test ===")
r = client.get("/api/v1/jobs/?limit=5&offset=0")
print(f'Limit 5, offset 0: {len(r.json()["items"])} items, total={r.json()["total"]}')
r = client.get("/api/v1/jobs/?limit=5&offset=5")
print(f'Limit 5, offset 5: {len(r.json()["items"])} items, total={r.json()["total"]}')

# Test node filter
print("\n=== Node filter test ===")
r = client.get("/api/v1/jobs/?node=node-alpha")
print(
    f'Node filter: {r.status_code}, items={len(r.json()["items"])}, total={r.json()["total"]}'
)

# Test combined filters
print("\n=== Combined filters test ===")
r = client.get("/api/v1/jobs/?status=RUNNING&node=node-alpha")
print(
    f'Status + node: {r.status_code}, items={len(r.json()["items"])}, total={r.json()["total"]}'
)

print("\n=== All manual tests completed ===")
