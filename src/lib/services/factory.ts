// Service factory - selects mock or real implementation based on environment
import { isLocalhost } from '@/lib/settings';
import { IJobService } from './types';
import { INodeService } from './types';
import { IAuthService } from './types';

// Mock implementations
import { MockJobService } from './mock/JobService.mock';
import { MockNodeService } from './mock/NodeService.mock';
import { MockAuthService } from './mock/AuthService.mock';

// Real implementations (stubs for future backend)
import { RealJobService } from './real/JobService.real';
import { RealNodeService } from './real/NodeService.real';
import { RealAuthService } from './real/AuthService.real';

// Auto-select based on localhost (same logic as settings.ts)
const USE_MOCK = isLocalhost();

export const services = {
  jobs: USE_MOCK ? new MockJobService() : new RealJobService(),
  nodes: USE_MOCK ? new MockNodeService() : new RealNodeService(),
  auth: USE_MOCK ? new MockAuthService() : new RealAuthService(),
};

// Export types for external use
export type { IJobService, INodeService, IAuthService };