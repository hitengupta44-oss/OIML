/// <reference types="vite/client" />

/**
 * Typed environment variables.
 *
 * Every VITE_ variable is compiled into the browser bundle — that is by
 * design and they are public. Fine for the project URL and the anon key;
 * exactly why the service-role key must never be one.
 */
interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string;
  readonly VITE_SUPABASE_ANON_KEY: string;
  readonly VITE_SPACE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
