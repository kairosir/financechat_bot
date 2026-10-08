import { createAuthClient } from '@neondatabase/auth';

export function createClient(baseUrl) {
  return createAuthClient(baseUrl);
}
