// En dev (npm run dev, port 3000) les appels sont relayes vers le serveur Python (tools/app.py, port 8765).
// En build, export statique dans web/out, servi directement par tools/app.py.
const API = "http://127.0.0.1:8765";
const isDev = process.env.NODE_ENV === "development";

/** @type {import('next').NextConfig} */
const config = isDev
  ? {
      devIndicators: false,
      async rewrites() {
        return ["api", "video", "thumb", "source", "frame", "static"].map((p) => ({
          source: `/${p}/:path*`,
          destination: `${API}/${p}/:path*`,
        }));
      },
    }
  : { output: "export", images: { unoptimized: true } };

export default config;
