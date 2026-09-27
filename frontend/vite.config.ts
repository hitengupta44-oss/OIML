import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import path from "node:path";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      // The standards JSON and the engine must be in the app shell: a
      // technician inside a shielded EMC chamber has no network, and
      // verdicts still have to be computed there.
      includeAssets: ["favicon.svg"],
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,woff2,json}"],
        // Never cache API traffic -- stale evaluations would be worse than
        // no evaluations.
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/fonts\.(googleapis|gstatic)\.com\//,
            handler: "CacheFirst",
            options: {
              cacheName: "fonts",
              expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 365 },
            },
          },
        ],
      },
      manifest: {
        name: "NAWI Type Evaluation",
        short_name: "NAWI",
        description:
          "OIML R 76 type-evaluation test reports for non-automatic weighing instruments",
        theme_color: "#0B5A64",
        background_color: "#F4F6F7",
        display: "standalone",
        orientation: "any",
        start_url: "/",
        icons: [
          { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      // One engine, one set of standards JSON, shared with the Python
      // service. A band value must exist in exactly one place.
      "@engine": path.resolve(__dirname, "../engine"),
      "@standards": path.resolve(__dirname, "../standards"),
      "@": path.resolve(__dirname, "src"),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
          supabase: ["@supabase/supabase-js"],
        },
      },
    },
  },
  server: { port: 5173 },
});
