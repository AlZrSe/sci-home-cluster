import type { IAuthService } from '../types';
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

export class RealAuthService implements IAuthService {
  private get apiBaseUrl(): string {
    return getSettings().apiBaseUrl;
  }

  async validateToken(token: string): Promise<boolean> {
    // TODO: Implement actual fetch call
    // const response = await fetch(`${this.apiBaseUrl}/auth/validate`, {
    //   method: 'POST',
    //   headers: requireAuthHeaders(),
    //   body: JSON.stringify({ token })
    // });
    // if (!response.ok) throw new ApiError(response.status, await response.text());
    // const result = await response.json();
    // return result.valid;
    throw new Error('RealAuthService.validateToken: Not implemented - backend not available');
  }
}