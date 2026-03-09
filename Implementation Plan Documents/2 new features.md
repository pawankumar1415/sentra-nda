# Feature: Batch Validation & Validate Page Dropdown

Two new features that extend the existing RAG pipeline and frontend.

---

## Feature 1 — Batch Validation

### What it does
A new API route `POST /api/batch-validate` accepts an Excel file (same format as `/api/ingest`). It parses the `5a)NDA MPPR` sheet, runs [run_validate()](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/validate.py#218-277) for every project that has a narrative, and streams back a consolidated JSON array of results — one result object per project.

This is **independent of the ingest route**; it validates straight from the uploaded file without touching the DB write path.

### What it does NOT do
It does not ingest / upsert into PostgreSQL — that is still the job of `/api/ingest`. The batch route only reads from the file + existing DB vector store for context retrieval.

---

## Feature 2 — Project Dropdown on Validate Page

### What it does
A new **lightweight** API route `POST /api/list-projects` accepts the same Excel file and returns only the list of project names parsed from the `5a)NDA MPPR` sheet — no embeddings, no DB calls.

On the frontend [ValidateView.tsx](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/frontend/src/views/ValidateView.tsx):
1. An "Upload Excel" button/dropzone appears above the Project Name field.
2. When the user uploads a file, the frontend calls `/api/list-projects` and populates a `<select>` dropdown with the returned project names.
3. The period is auto-filled from the filename (e.g. `P08`).
4. The narrative textarea is auto-filled with the selected project's narrative from the API response.
5. The user can still manually type a project name if they prefer (dropdown has a "Type manually" fallback).

---

## Proposed Changes

### Backend — `rag_function/`

---

#### [MODIFY] [ingest.py](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/ingest.py)

Extract a new **public** helper `list_projects_from_bytes(file_bytes, filename)` that calls [parse_excel()](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/ingest.py#89-206) and returns a lightweight list:
```python
[{ "project_name": str, "narrative_text": str, "period": str }, ...]
```
This avoids duplicating the parsing logic across routes.

---

#### [NEW] [batch_validate.py](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/batch_validate.py)

New module containing `run_batch_validate(file_bytes, filename, top_k)`:
1. Calls `list_projects_from_bytes()` from the updated [ingest.py](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/ingest.py) to get all projects + narratives.
2. For each project that has a `narrative_text`, calls [run_validate()](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/validate.py#218-277) from [validate.py](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/validate.py).
3. Returns a list of result dicts with a `project_name` key prepended to each.
4. Handles per-project errors gracefully (marks a project as `error: true` without stopping the whole batch).

---

#### [MODIFY] [function_app.py](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/rag_function/function_app.py)

Add two new routes:

**Route: `POST /api/list-projects`**
- Accepts the same multipart/raw Excel upload as `/api/ingest`.
- Calls `list_projects_from_bytes()`.
- Returns `{ "period": "P08", "projects": [{ "project_name": str, "narrative_text": str }, ...] }`.
- No DB calls — pure parsing.

**Route: `POST /api/batch-validate`**
- Accepts the same multipart/raw Excel upload.
- Calls `run_batch_validate()`.
- Returns `{ "period": "P08", "total": N, "results": [...] }`.
- Each `result` is the standard validate JSON shape plus a `project_name` field.

---

### Frontend — `frontend/src/`

---

#### [MODIFY] [api.ts](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/frontend/src/services/api.ts)

Add two new typed API functions:
- `listProjects(key, file)` → calls `POST /api/list-projects`, returns `{ period, projects }`.
- `batchValidate(key, file)` → calls `POST /api/batch-validate`, returns `{ period, total, results }`.

---

#### [MODIFY] [ValidateView.tsx](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/frontend/src/views/ValidateView.tsx)

Add at top of the form card, **before** the existing Project Name / Period inputs:

1. **File Upload zone** — a drag-and-drop or click-to-upload control. Shows filename when loaded.
2. Once a file is uploaded, call `listProjects()` to populate:
   - A **`<select>` dropdown** for Project Name (replaces the plain text input when a file is loaded).
   - Auto-fill the **Period** field from the API response.
   - Auto-fill the **narrative textarea** with the selected project's `narrative_text` from the response.
3. When the user changes the dropdown selection, the narrative textarea updates automatically.
4. If no file is uploaded, the form works exactly as today (manual text inputs).

---

#### [NEW] [BatchValidateView.tsx](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/frontend/src/views/BatchValidateView.tsx)

New view for batch validation:
- **Left panel**: API Key input + Excel file upload.
- **Right panel**: Progress indicator while processing, then a filterable/sorted results table showing:
  - Project Name, Verdict badge (PASS/FAIL/WARN), Compliance Score, # Issues, EAC flag.
  - Click a row to expand the full validation detail (same card components from ValidateView).

---

#### [MODIFY] [App.tsx](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/frontend/src/App.tsx)

Register the new `/batch-validate` route pointing to `BatchValidateView`.

---

#### [MODIFY] [Sidebar.tsx](file:///c:/Users/rahul/Repos/Sentra%20Project%20BSBI/Custom%20Solution/frontend/src/components/Sidebar.tsx)

Add a "Batch Validate" nav item with an appropriate icon (e.g. `ListChecks` from lucide-react).

---

## Verification Plan

### Automated (existing test suite)
Run after backend changes:
```powershell
cd "c:\Users\rahul\Repos\Sentra Project BSBI\Custom Solution"
venv\Scripts\python test_rag.py
```
This validates that the existing DB, embedder, ingest, and validate pipelines still work correctly after the refactor of `ingest.py`.

### Manual API Tests (curl / Postman)
After deploying or running `func start` locally from `rag_function/`:

```bash
# Test 1: list-projects route
curl -X POST "http://localhost:7071/api/list-projects?filename=P08.xlsx" \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@P08.xlsx"
# Expect: { "period": "P08", "projects": [ { "project_name": "...", "narrative_text": "..." }, ... ] }

# Test 2: batch-validate route  
curl -X POST "http://localhost:7071/api/batch-validate?filename=P08.xlsx" \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@P08.xlsx"
# Expect: { "period": "P08", "total": N, "results": [ { "project_name": "...", "overall_verdict": "...", ... } ] }
```

### Manual Frontend Tests
1. Start the frontend dev server: `cd frontend && npm run dev`
2. Navigate to `/validate`:
   - Upload an MPPR Excel file using the new upload zone.
   - Confirm the dropdown populates with project names.
   - Select a project — confirm the narrative textarea auto-fills.
   - Confirm period field auto-fills from filename.
   - Click Validate & Verify — confirm result displays as before.
3. Navigate to `/batch-validate`:
   - Upload the same MPPR Excel file.
   - Confirm results table appears after processing with one row per project.
   - Click a row — confirm the full detail card expands inline.
