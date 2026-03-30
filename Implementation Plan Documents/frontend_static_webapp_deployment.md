# Frontend Deployment — Azure Static Web Apps

## Overview

The NDA Validation Tool frontend (React 19 + Vite) is hosted on **Azure Static Web Apps** (Free tier, West Europe).

| Property | Value |
|---|---|
| Resource Name | `nda-custom-frontend-static` |
| Resource Group | `sellafield-dpmo-dev` |
| Region | West Europe |
| SKU | Free |
| URL | https://lemon-bay-04878fd03.4.azurestaticapps.net |
| Backend Function App | `nda-python-backend` |

---

## Why Azure Static Web Apps

The original plan was Azure App Service Web App (Free tier, F1) but it was ruled out due to:

- **CPU quota exhaustion** — Free tier allows 60 CPU minutes/day; repeated crash/restart cycles during debugging consumed the daily quota.
- **Basic VM quota** — The subscription has zero Basic VM quota in UK South, blocking upgrade to B1.
- **UK South unavailable** — Azure Static Web Apps does not support UK South; West Europe was used instead (no latency concern for a static asset CDN).

Azure Static Web Apps is the correct choice for a pre-built React SPA:
- No CPU quota limits on static file serving.
- Built-in SPA routing fallback (no `server.js` needed).
- Free SSL certificate provisioned automatically.
- Global CDN distribution included.

---

## Files

### `frontend/public/staticwebapp.config.json`

Handles SPA client-side routing — all unknown paths fall back to `index.html` so React Router can take over:

```json
{
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/assets/*", "/*.js", "/*.css", "/*.svg", "/*.png", "/*.ico"]
  }
}
```

This file is placed in `public/` so Vite copies it to `dist/` during build.

---

## Deployment Steps

### Prerequisites

```bash
npm install -g @azure/static-web-apps-cli
```

### 1. Build the frontend

```bash
cd frontend
npm run build
```

Ensure `frontend/.env.production` (or environment variables at build time) contains:

```
VITE_AZURE_FUNCTION_KEY=<your-function-key>
VITE_AZURE_AGENT_FUNCTION_KEY=<your-agent-function-key>
VITE_API_BASE_URL=https://nda-python-backend.azurewebsites.net/api
```

### 2. Get the deployment token

```bash
az staticwebapp secrets list \
  --name nda-custom-frontend-static \
  --resource-group sellafield-dpmo-dev \
  --query "properties.apiKey" -o tsv
```

### 3. Deploy

```bash
swa deploy dist \
  --deployment-token <TOKEN> \
  --env production
```

Run from the `frontend/` directory. The SWA CLI downloads `StaticSitesClient.exe` on first run.

---

## CORS Configuration

The backend Function App must allowlist the Static Web App URL:

```bash
az functionapp cors add \
  --resource-group sellafield-dpmo-dev \
  --name nda-python-backend \
  --allowed-origins https://lemon-bay-04878fd03.4.azurestaticapps.net
```

Current allowed origins on `nda-python-backend`:

- `https://portal.azure.com`
- `http://localhost:5173`
- `http://127.0.0.1:5173`
- `https://nda-custom-frontend.azurewebsites.net` *(legacy — can be removed)*
- `https://lemon-bay-04878fd03.4.azurestaticapps.net`

---

## Custom Domain (Optional)

The auto-generated subdomain (`lemon-bay-04878fd03`) cannot be changed. To use a clean URL, map a custom domain:

```bash
az staticwebapp hostname set \
  --name nda-custom-frontend-static \
  --resource-group sellafield-dpmo-dev \
  --hostname app.yourdomain.com
```

Azure provisions a free SSL certificate automatically. Add the CNAME/TXT records at your DNS registrar as instructed.

---

## Redeployment

For any future frontend changes:

```bash
cd frontend
npm run build
swa deploy dist --deployment-token <TOKEN> --env production
```

The deployment token does not expire and can be stored as a CI/CD secret for automated deployments.

---

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| `az webapp deploy` returns 400 | OneDeploy / Oryx tries to rebuild | Use SWA CLI instead |
| Publishing credentials show `REDACTED` | SCM basic auth disabled | `az resource update ... properties.allow=true` |
| `LocationNotAvailableForResourceType` | UK South not supported for Static Web Apps | Use `westeurope` |
| App shows Application Error after deploy | `server.js` used ES module syntax without `package.json` | Not applicable with Static Web Apps |
| Free tier quota exceeded | F1 has 60 CPU min/day limit | Switched to Static Web Apps (no quota) |