import "server-only";
import { neon } from "@neondatabase/serverless";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL is not set (web/.env.local locally, Project Settings → Environment Variables on Vercel)");
}

/** Neon's HTTP driver: one round trip per query, no pooled connections to leak on serverless. */
export const sql = neon(process.env.DATABASE_URL);

// Postgres int8/numeric come back as strings; every query below casts what it needs with ::int / ::float8.
export type Row = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
