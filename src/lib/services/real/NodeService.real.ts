import type { INodeService } from '../types';
import type { NodeSpec } from '@/types';
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

export class RealNodeService implements INodeService {
  private get apiBaseUrl(): string {
    return getSettings().apiBaseUrl;
  }

  async listNodes(): Promise<NodeSpec[]> {
    // TODO: Implement actual fetch call
    throw new Error('RealNodeService.listNodes: Not implemented - backend not available');
  }

  async getNode(id: string): Promise<NodeSpec> {
    // TODO: Implement actual fetch call
    throw new Error('RealNodeService.getNode: Not implemented - backend not available');
  }
}