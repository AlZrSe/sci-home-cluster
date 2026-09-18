// Service interfaces matching the existing api.ts exports
import type { 
  JobListResult, JobSpec, JobState, JobStatus, NodeSpec, JobMetrics, JobQuery
} from '@/types';

// Job service interface
export interface IJobService {
  listJobs(q: JobQuery): Promise<JobListResult>;
  getJob(id: string): Promise<JobState>;
  getJobMetrics(id: string): Promise<JobMetrics>;
  getJobLogs(id: string): Promise<string[]>;
  streamJobLogs(
    id: string,
    onLine: (line: string) => void,
    onStatus: (s: 'connecting' | 'open' | 'closed') => void
  ): () => void;
  createJob(spec: JobSpec): Promise<JobState>;
  retryJob(id: string): Promise<JobState>;
  cancelJob(id: string): Promise<JobState>;
  deleteJob(id: string): Promise<void>;
}

// Node service interface
export interface INodeService {
  listNodes(): Promise<NodeSpec[]>;
  getNode(id: string): Promise<NodeSpec>;
}

// Auth service interface
export interface IAuthService {
  validateToken(token: string): Promise<boolean>;
}