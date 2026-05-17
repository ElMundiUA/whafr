/// <reference path="../.astro/types.d.ts" />

import type { SessionUser } from "@/lib/auth";

declare global {
  namespace App {
    interface Locals {
      user: SessionUser | null;
    }
  }
}

interface ImportMetaEnv {
  readonly PUBLIC_LIGHTHOUSE_API_BASE: string;
  readonly AUTH0_DOMAIN: string;
  readonly AUTH0_CLIENT_ID: string;
  readonly AUTH0_CLIENT_SECRET: string;
  readonly AUTH0_AUDIENCE: string;
  readonly AUTH0_SCOPE: string;
  readonly SESSION_SECRET: string;
  readonly ADMIN_EMAIL: string;
  readonly PADDLE_VENDOR_ID: string;
  readonly PADDLE_API_KEY: string;
  readonly PADDLE_PUBLIC_KEY: string;
  readonly PADDLE_PRODUCT_PRO_MONTHLY: string;
  readonly PADDLE_ENV: string;
  readonly LIGHTHOUSE_DATABASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
