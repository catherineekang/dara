/**
 * DARA console — static file server plus an /api proxy to the Flask backend.
 *
 * Zero dependencies on purpose. There is no `npm install`, no build step and no
 * bundler: `node server.js` is the whole story. The previous setup failed to
 * open because the dev server bound only the IPv6 loopback, so this binds
 * 127.0.0.1 explicitly and prints the exact URL it is reachable at.
 *
 * Proxying /api through this server means the browser talks to ONE origin, so
 * there is no CORS to configure and the page can use plain relative URLs.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT) || 3000;
const HOST = "127.0.0.1";
const API_HOST = process.env.API_HOST || "127.0.0.1";
const API_PORT = Number(process.env.API_PORT) || 8000;
const ROOT = path.join(__dirname, "public");

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

/** Forward /api/* to Flask, streaming the response straight through.
 *  Streaming matters: a run is server-sent events, and buffering it here would
 *  deliver the whole debate in one lump at the end instead of turn by turn. */
function proxy(req, res) {
  const upstream = http.request(
    { host: API_HOST, port: API_PORT, path: req.url, method: req.method, headers: req.headers },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      up.pipe(res);
    },
  );

  upstream.on("error", (err) => {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        detail:
          `Cannot reach the API at http://${API_HOST}:${API_PORT} (${err.code}). ` +
          `Start it with:  cd backend && python app.py`,
      }),
    );
  });

  req.pipe(upstream);
}

function serveFile(res, filePath) {
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Not found");
      return;
    }
    res.writeHead(200, {
      "Content-Type": TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream",
      "Cache-Control": "no-cache",
    });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  if (req.url.startsWith("/api/")) return proxy(req, res);

  // Strip the query string, then refuse anything that climbs out of public/.
  const rel = decodeURIComponent(req.url.split("?")[0]);
  const filePath = path.join(ROOT, rel === "/" ? "index.html" : rel);
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403, { "Content-Type": "text/plain" });
    res.end("Forbidden");
    return;
  }
  fs.stat(filePath, (err, stat) => {
    if (!err && stat.isDirectory()) return serveFile(res, path.join(filePath, "index.html"));
    serveFile(res, filePath);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`\n  DARA console   http://${HOST}:${PORT}`);
  console.log(`  API proxied to http://${API_HOST}:${API_PORT}\n`);
});
