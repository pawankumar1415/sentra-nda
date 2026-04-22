# NDA Narrative Validation — React Frontend

React 19 + TypeScript + Vite frontend for the Custom RAG approach (`nda-python-backend`). Provides narrative validation, batch processing, conversational chat, and data ingestion — all behind JWT authentication.

---

## Views

| Route | View | Description |
|---|---|---|
| `/login` | LoginView | Sign in / Register — public, only entry without a token |
| `/chat` | ChatView | Conversational RAG assistant with session memory |
| `/validate` | ValidateView | Single narrative validation with compliance score |
| `/batch-validate` | BatchValidateView | Batch validate all projects in an MPPR Excel |
| `/ingest` | IngestView | Upload MPPR and EAC variance Excel files |
| `/admin` | AdminView | User management — admin accounts only |

---

## Tech Stack

| Package | Version | Purpose |
|---|---|---|
| React | 19 | UI framework |
| React Router | 7 | Client-side routing |
| TypeScript | 5 | Type safety |
| Vite | 6 | Build tool + dev server |
| react-markdown | — | Render agent markdown responses |
| lucide-react | — | Icons |
| xlsx | — | Client-side Excel export (batch results) |

---

## Setup

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173 → redirects to /login
```

Register the first account — it becomes admin automatically.

---

## Configuration

The app reads backend URLs from environment variables. Create a `.env.local` file in the `frontend/` folder:

```env
VITE_API_BASE_URL=http://localhost:7071          # RAG function (nda-python-backend)
VITE_AGENT_API_BASE_URL=http://localhost:7072     # Agent function (nda-foundry-api)
VITE_AZURE_FUNCTION_KEY=your-function-host-key   # Required for agent endpoints in production
```

In production these are set as Static Web App environment variables.

---

## Authentication

- JWT token is stored in `localStorage` under the key `nda_auth` and survives page refresh
- `ProtectedRoute` redirects to `/login` if no valid token is present
- Two roles: **standard** (all features) and **admin** (standard + user management)
- Admin-only routes return `403` from the backend if called by a standard user

---

## Build & Deploy

```bash
npm run build       # Output to dist/
npm run preview     # Preview the production build locally
```

Deploy the `dist/` folder to Azure Static Web Apps or any static host.

---

## Key Files

```
src/
├── context/
│   └── AuthContext.tsx        # JWT state (token, username, is_admin)
├── components/
│   ├── Sidebar.tsx            # Navigation + logged-in user + logout
│   └── ProtectedRoute.tsx     # Auth guard wrapper
├── views/
│   ├── LoginView.tsx          # Sign in / Register tab switcher
│   ├── AdminView.tsx          # User management table (admin only)
│   ├── ChatView.tsx           # Conversational RAG chat
│   ├── ValidateView.tsx       # Single narrative validation
│   ├── BatchValidateView.tsx  # Batch validation with progress bar + export
│   └── IngestView.tsx         # MPPR + EAC file upload
└── services/
    └── api.ts                 # All API calls — JWT header attached automatically
```