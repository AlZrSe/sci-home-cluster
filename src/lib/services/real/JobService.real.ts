import type { IJobService } from '../types';
import type {
  JobListResult, JobSpec, JobState, JobStatus, JobMetrics, JobQuery
} from '@/types';
import { getSettings } from '@/lib/settings';

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const delay = (ms = 260) => new Promise((r) => setTimeout(r, ms));

function requireAuthHeaders() {
  const { token } = getSettings();
  if (!token) throw new ApiError(401, "Missing bearer token");
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json'
  };
}

export class RealJobService implements IJobService {
  private get apiBaseUrl(): string {
    return getSettings().apiBaseUrl;
  }

  async listJobs(q: JobQuery = {}): Promise<JobListResult> {
    // TODO: Implement actual fetch call
    // const response = await fetch(`${this.apiBaseUrl}/jobs`, {
    //   method: 'GET',
    //   headers: requireAuthHeaders(),
    //   body: JSON.stringify(q)
    // });
    // if (!response.ok) throw new ApiError(response.status, await response.text());
    // return response.json();
    throw new Error('RealJobService.listJobs: Not implemented - backend not available');
  }

  async getJob(id: string): Promise<JobState> {
    // TODO: Implement actual fetch call
    throw new Error('RealJobService.getJob: Not implemented - backend not available');
  }

  async getJobMetrics(id: string): Promise<JobMetrics> {
    // TODO: Implement actual fetch call
    throw new Error('RealJobService.getJobMetrics: Not implemented - backend not available');
  }

  async getJobLogs(id: string): Promise<string[]> {
    // TODO: Implement actual fetch call
    throw new Error('RealJobService.getJobLogs: Not implemented - backend not available');
  }

  streamJobLogs(
    id: string,
    onLine: (line: string) => void,
    onStatus: (s: "connecting" | "open" | "closed") => void
  ): () => void {
    // TODO: Implement actual WebSocket connection
    throw new Error('RealJobService.streamJobLogs: Not implemented - backend not available');
    // Returns unsubscribe function
    return () => {};
  }

  async createJob(spec: JobSpec): Promise<JobState> {
    // TODO: Implement actual fetch call with multipart/form-data
    throw new Error('RealJobService.createJob: Not implemented - backend not available');
  }

  async retryJob(id: string): Promise<JobState> {
    // TODO: Implement actual fetch call
    throw new Error('RealJobService.retryJob: Not implemented - backend not available');
  }

  async cancelJob(id: string): Promise<JobState> {
    // TODO: Implement actual fetch call
    throw new Error('RealJobService.cancelJob: Not implemented - backend not available');
  }

  async deleteJob(id: string): Promise<void> {
    // TODO: Implement actual fetch call
    throw new Error('RealJobService.deleteJob: Not implemented - backend not available');
  }
}