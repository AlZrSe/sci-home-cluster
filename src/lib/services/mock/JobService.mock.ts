import type { IJobService } from '../types';
import type {
  JobListResult, JobSpec, JobState, JobStatus, JobMetrics, JobQuery
} from '@/types';
// Import everything we need from the existing mock-server
import {
  jobs,
  logsFor,
  metricsFor,
  nodes,
  subscribeLogs,
  makeSpec
} from '@/lib/mock-server';

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const delay = (ms = 260) => new Promise((r) => setTimeout(r, ms));

function requireToken() {
  // For mock service, we don't actually validate tokens
  // The mock always works regardless of token (matching current behavior)
  // In real implementation, this would validate against backend
  return true;
}

export class MockJobService implements IJobService {
  async listJobs(q: JobQuery = {}): Promise<JobListResult> {
    await delay();
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    
    const limit = q.limit ?? 10;
    const offset = q.offset ?? 0;
    const filtered = jobs.filter((j) => {
      if (q.status && q.status !== "ALL" && j.status !== q.status) return false;
      if (q.node && q.node !== "ALL" && j.node_id !== q.node) return false;
      if (q.search) {
        const s = q.search.toLowerCase();
        if (!j.spec.name.toLowerCase().includes(s) && !j.job_id.toLowerCase().includes(s))
          return false;
      }
      return true;
    });
    return { items: filtered.slice(offset, offset + limit), total: filtered.length };
  }

  async getJob(id: string): Promise<JobState> {
    await delay();
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    const job = jobs.find((j) => j.job_id === id);
    if (!job) throw new ApiError(404, `Job ${id} not found`);
    return job;
  }

  async getJobMetrics(id: string): Promise<JobMetrics> {
    await delay(320);
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    return metricsFor(id);
  }

  async getJobLogs(id: string): Promise<string[]> {
    await delay(200);
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    return [...logsFor(id)];
  }

  streamJobLogs(
    id: string,
    onLine: (line: string) => void,
    onStatus: (s: "connecting" | "open" | "closed") => void
  ): () => void {
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    return subscribeLogs(id, onLine, onStatus);
  }

  async createJob(spec: JobSpec): Promise<JobState> {
    await delay(520);
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    const job: JobState = {
      job_id: `job-${1051 + jobs.filter((j) => j.job_id.startsWith("job-10")).length}`,
      spec,
      status: "PENDING",
      created_at: new Date().toISOString(),
      retry_count: 0,
    };
    jobs.unshift(job);
    return job;
  }

  async retryJob(id: string): Promise<JobState> {
    await delay(360);
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    const job = await this.getJob(id);
    job.status = "PENDING";
    job.retry_count += 1;
    job.error = undefined;
    job.exit_code = undefined;
    job.completed_at = undefined;
    return job;
  }

  async cancelJob(id: string): Promise<JobState> {
    await delay(360);
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    const job = await this.getJob(id);
    job.status = "CANCELLED";
    job.completed_at = new Date().toISOString();
    return job;
  }

  async deleteJob(id: string): Promise<void> {
    await delay(360);
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    const i = jobs.findIndex((j) => j.job_id === id);
    if (i >= 0) jobs.splice(i, 1);
  }
}