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
  readonly PADDLE_CLIENT_TOKEN: string;
  readonly PADDLE_API_KEY: string;
  readonly PADDLE_WEBHOOK_SECRET: string;
  readonly PADDLE_PRODUCT_PRO: string;
  readonly PADDLE_PRICE_PRO_MONTHLY: string;
  readonly PADDLE_PRICE_PRO_ANNUAL: string;
  readonly PADDLE_PRICE_TEAM_MONTHLY: string;
  readonly PADDLE_PRICE_TEAM_ANNUAL: string;
  readonly PADDLE_ENVIRONMENT: string;
  readonly LIGHTHOUSE_DATABASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
