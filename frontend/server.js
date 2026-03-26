/**
 * server.js — Static file server for the React SPA on Azure Web App (Node 24).
 *
 * Serves all files from ./dist.  Any path that does not match a real file
 * falls back to index.html so React Router can handle client-side navigation.
 *
 * Usage (set as Azure Web App startup command):
 *   node server.js
 */

import { createServer } from 'http';
import { createReadStream, existsSync, statSync } from 'fs';
import { join, extname, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const DIST_DIR  = resolve(__dirname, 'dist');
const PORT      = process.env.PORT || 8080;

const MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript',
    '.mjs':  'application/javascript',
    '.css':  'text/css',
    '.json': 'application/json',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.svg':  'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2':'font/woff2',
    '.ttf':  'font/ttf',
    '.webmanifest': 'application/manifest+json',
};

createServer((req, res) => {
    const url      = req.url.split('?')[0];          // strip query string
    const filePath = join(DIST_DIR, url);

    // Serve the real file if it exists and is not a directory
    if (existsSync(filePath) && statSync(filePath).isFile()) {
        const mime = MIME[extname(filePath)] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': mime });
        createReadStream(filePath).pipe(res);
        return;
    }

    // SPA fallback — let React Router handle the route
    const index = join(DIST_DIR, 'index.html');
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    createReadStream(index).pipe(res);
}).listen(PORT, () => {
    console.log(`NDA frontend listening on port ${PORT}`);
});