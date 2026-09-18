// Main services export
export { services } from './factory';
export type { IJobService, INodeService, IAuthService } from './types';

// Re-export mock implementations for testing if needed
export { MockJobService } from './mock/JobService.mock';
export { MockNodeService } from './mock/NodeService.mock';
export { MockAuthService } from './mock/AuthService.mock';

// Re-export real implementations
export { RealJobService } from './real/JobService.real';
export { RealNodeService } from './real/NodeService.real';
export { RealAuthService } from './real/AuthService.real';