/**
 * server.js — Static file server for the React SPA on Azure Web App (Node 24).
 * Uses CommonJS require so no package.json / "type":"module" is needed.
 *
 * Startup command: node server.js
 */

const http   = require('http');
const fs     = require('fs');
const path   = require('path');

const DIST_DIR = path.join(__dirname, 'dist');
const PORT     = process.env.PORT || 8080;

const MIME = {
    '.html':        'text/html; charset=utf-8',
    '.js':          'application/javascript',
    '.mjs':         'application/javascript',
    '.css':         'text/css',
    '.json':        'application/json',
    '.png':         'image/png',
    '.jpg':         'image/jpeg',
    '.svg':         'image/svg+xml',
    '.ico':         'image/x-icon',
    '.woff':        'font/woff',
    '.woff2':       'font/woff2',
    '.ttf':         'font/ttf',
    '.webmanifest': 'application/manifest+json',
};

http.createServer(function (req, res) {
    var url      = req.url.split('?')[0];
    var filePath = path.join(DIST_DIR, url);

    if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
        var mime = MIME[path.extname(filePath)] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': mime });
        fs.createReadStream(filePath).pipe(res);
        return;
    }

    // SPA fallback
    var index = path.join(DIST_DIR, 'index.html');
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    fs.createReadStream(index).pipe(res);

}).listen(PORT, function () {
    console.log('NDA frontend listening on port ' + PORT);
});