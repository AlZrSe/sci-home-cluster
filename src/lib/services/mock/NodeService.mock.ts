import type { INodeService } from '../types';
import type { NodeSpec } from '@/types';
import { nodes } from '@/lib/mock-server';

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
  return true;
}

export class MockNodeService implements INodeService {
  async listNodes(): Promise<NodeSpec[]> {
    await delay();
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    return nodes;
  }

  async getNode(id: string): Promise<NodeSpec> {
    await delay();
    if (!requireToken()) throw new ApiError(401, "Missing bearer token");
    const node = nodes.find((n) => n.node_id === id);
    if (!node) throw new ApiError(404, `Node ${id} not found`);
    return node;
  }
}