import type { IAuthService } from '../types';
import { validateToken } from '@/lib/mock-server';

export class MockAuthService implements IAuthService {
  async validateToken(token: string): Promise<boolean> {
    // Note: mock-server.ts validateToken doesn't require actual token validation
    // it just checks length >= 8, matching the current behavior
    return validateToken(token);
  }
}