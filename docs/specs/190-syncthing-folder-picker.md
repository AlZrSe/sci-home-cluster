# Spec: Syncthing Folder Picker in Job Submission Form

**Issue**: #190 — "Pick a folder inside the Syncthing folder as input in the job submission form"
**Labels**: `enhancement`, `ready-for-grooming` → `groomed`, `frontend`, `integration`
**Author**: PM
**Date**: 2026-11-21

---

## 1. Verified Problem Statement

The job submission form (`/jobs/new`) currently requires users to manually type the `input` and `output` paths as free-text strings. These paths are relative to the Syncthing folder root (e.g., `data/in`, `data/out`). There is no visual way to browse the actual Syncthing folder structure to see what directories exist and pick one.

This creates friction and error potential:
- Users must know the exact folder structure and naming convention
- Typos in path strings lead to jobs that cannot find their input data
- No discovery of what folders are actually available in the shared Syncthing folder

The Syncthing folder is already monitored by the backend (`backend/services/syncthing_service.py`) and its status is exposed via `GET /api/v1/syncthing/status`, which returns `jobs_folder` and `nodes_folder` info. However, there is no API to list arbitrary subdirectories under the Syncthing root, and no UI component to browse and pick a folder.

---

## 2. Root Cause

This is a missing feature — the folder picker UI and the backend API to support it were never built. The Syncthing folder structure exists and is accessible, but:
1. No backend endpoint to list directories under `SYNCTHING_ROOT`
2. No frontend component for folder selection in the job submission form
3. The `input`/`output` fields in the form are plain text inputs with no browsing capability

---

## 3. Open Questions — Resolved

### Q1 — Which Syncthing subfolder(s) should be browsable? **Resolved: The entire Syncthing root.**

The Syncthing root (`settings.SYNCTHING_ROOT`) contains `jobs/`, `nodes/`, and potentially user-created folders for input data. The picker should expose the full tree under the root so users can navigate to any folder (e.g., `data/`, `projects/`, `shared/`). The backend already serves `jobs/` and `nodes/` status; the new endpoint will list all directories.

### Q2 — Should the picker show files or only directories? **Resolved: Directories only.**

The `input` and `output` fields in `JobSpec.paths` are directory paths. The picker should only show directories (folders), not files.

### Q3 — How deep should the tree go? **Resolved: Full recursive depth, with lazy loading.**

The folder tree should support arbitrary depth. The API will return a flat list of all directories (relative paths) for simplicity, and the UI will build a tree structure client-side. For very large trees, lazy loading (load children on expand) can be added later if needed.

### Q4 — Should the picker filter to only "input-like" folders? **Resolved: No filter; show all directories.**

Users may have various folder structures. The picker shows everything; the user decides what is an input folder. Profiles already provide sensible defaults (e.g., `data/vasp/in`).

### Q5 — What about the `output` field? **Resolved: Same picker for both input and output.**

The picker component will be reusable for both the `input` and `output` fields in the form. The user can pick different folders for each.

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| New backend endpoint `GET /api/v1/syncthing/folders` to list directories under `SYNCTHING_ROOT` | File listing (only directories) |
| New frontend `FolderPicker` component (reusable for input/output) | Lazy-loading tree (v1 returns flat list; tree built client-side) |
| Integration into `jobs.new.tsx` form for `input` and `output` fields | Syncthing GUI integration (separate iframe already exists) |
| API contract (request/response schemas) | Folder creation/deletion (read-only picker) |
| TypeScript types and Zod validation | Real-time filesystem watcher in UI |
| Unit and integration tests | Permissions/ACLs on folders |

---

## 5. Implementation Approach

### 5.1 Backend: New Syncthing Folders Endpoint

**File**: `backend/api/v1/syncthing.py` (add to existing router)

Add a new endpoint:
```python
@router.get(
    "/folders",
    response_model=SyncthingFolderList,
    responses={503: {"model": HTTPValidationError, "description": "Syncthing not configured"}},
)
async def list_syncthing_folders(
    request: Request, payload: dict = Depends(get_current_token_payload)
) -> SyncthingFolderList:
    """
    List all directories under the Syncthing root folder.
    Returns a flat list of relative paths for client-side tree building.
    """
    # Reuse existing Syncthing unavailable check from get_syncthing_status
    if not settings.SYNCTHING_ROOT or not Path(settings.SYNCTHING_ROOT).exists():
        raise syncthing_unavailable(...)

    root = Path(settings.SYNCTHING_ROOT)
    folders: list[str] = []

    for dirpath, dirnames, _ in os.walk(root):
        # Get relative path from root
        rel = Path(dirpath).relative_to(root)
        if rel != Path("."):
            folders.append(str(rel))

        # Skip hidden directories (starting with .)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

    # Sort for consistent ordering
    folders.sort()

    return SyncthingFolderList(root=settings.SYNCTHING_ROOT, folders=folders)
```

**New Schemas** (`shared/schemas/syncthing_folders.py`):
```python
class SyncthingFolderList(BaseModel):
    """List of directories under the Syncthing root."""
    root: str
    folders: list[str]  # Relative paths, e.g., ["data", "data/in", "projects", "projects/my-proj"]
```

**Error Handling**: Returns `503 SYNCTHING_UNAVAILABLE` if `SYNCTHING_ROOT` is not configured or not accessible, consistent with `/status` and `/scan`.

### 5.2 Frontend: API Service Extension

**File**: `frontend/src/services/types.ts` (add to `ClusterService` interface)
```typescript
getSyncthingFolders(): Promise<SyncthingFolderList>;
```

**File**: `frontend/src/services/http.ts` (add to `httpService`)
```typescript
getSyncthingFolders: () => request<SyncthingFolderList>("/syncthing/folders"),
```

**File**: `frontend/src/services/mock.ts` (add mock implementation)
```typescript
async getSyncthingFolders(): Promise<SyncthingFolderList> {
  return {
    root: "/syncthing",
    folders: ["data", "data/in", "data/out", "projects", "projects/my-project", "shared"],
  };
}
```

**New Types** (`frontend/src/services/types.ts`):
```typescript
export interface SyncthingFolderList {
  root: string;
  folders: string[];  // Relative paths from root
}
```

### 5.3 Frontend: FolderPicker Component

**File**: `frontend/src/components/FolderPicker.tsx` (new)

A reusable component that:
- Takes a `value` (current relative path string) and `onChange` callback
- Fetches folder list from `getSyncthingFolders()` on mount
- Builds a tree structure from the flat list client-side
- Renders a dropdown/button that opens a tree view
- Supports keyboard navigation (arrow keys, Enter to select, Escape to close)
- Shows the selected path or "Select folder..." placeholder
- Has a "Clear" option to set empty string

**Tree Building Logic** (client-side):
```typescript
function buildTree(folders: string[]): FolderNode[] {
  const root: FolderNode = { name: "", path: "", children: [], isRoot: true };
  const pathMap = new Map<string, FolderNode>();
  pathMap.set("", root);

  for (const folder of folders) {
    const parts = folder.split("/");
    let currentPath = "";
    for (const part of parts) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      if (!pathMap.has(currentPath)) {
        const node: FolderNode = {
          name: part,
          path: currentPath,
          children: [],
          parent: pathMap.get(currentPath.replace(`/${part}`, "") || "")!,
        };
        pathMap.set(currentPath, node);
        pathMap.get(currentPath.replace(`/${part}`, "") || "")!.children.push(node);
      }
    }
  }
  return root.children;
}
```

**UI Design**:
- Trigger: A button showing the current path (or placeholder)
- Popover: `shadcn/ui` Popover with a tree view using `Collapsible` components
- Each node: `Collapsible` with folder name, click to select, expand/collapse children
- Selected path highlighted
- Footer: "Clear selection" button

### 5.4 Frontend: Integration into Job Submission Form

**File**: `frontend/src/routes/jobs.new.tsx`

Replace the `input` and `output` `Input` fields (lines 291-298) with `FolderPicker` components:

```tsx
import { FolderPicker } from "@/components/FolderPicker";

// In the form, replace:
<div className="space-y-2">
  <Label htmlFor="input">Input path</Label>
  <FolderPicker
    id="input"
    value={values.input || ""}
    onChange={(val) => form.setValue("input", val)}
    placeholder="Select input folder..."
  />
</div>
<div className="space-y-2">
  <Label htmlFor="output">Output path</Label>
  <FolderPicker
    id="output"
    value={values.output || ""}
    onChange={(val) => form.setValue("output", val)}
    placeholder="Select output folder..."
  />
</div>
```

The `FolderPicker` will work with `react-hook-form` via the `onChange` callback calling `form.setValue()`.

The existing schema validation (`z.string().optional()`) remains unchanged — the picker just provides a better UX for filling the same string fields.

### 5.5 Shared Schemas Update

**File**: `shared/schemas/syncthing_folders.py` (new file)
```python
"""
Syncthing folder listing schemas.
"""
from pydantic import BaseModel, Field
from typing import List

class SyncthingFolderList(BaseModel):
    """List of directories under the Syncthing root."""
    root: str = Field(..., description="Absolute path to Syncthing root")
    folders: List[str] = Field(
        default_factory=list,
        description="Relative directory paths under the root, sorted"
    )

    model_config = {"from_attributes": True}
```

**File**: `shared/schemas/__init__.py` — export the new schema.

---

## 6. API Contract

### 6.1 Request
```
GET /api/v1/syncthing/folders
Authorization: Bearer <token>
```

### 6.2 Success Response (200)
```json
{
  "root": "/tmp/syncthing",
  "folders": [
    "data",
    "data/in",
    "data/out",
    "projects",
    "projects/my-project",
    "shared"
  ]
}
```

### 6.3 Error Responses

**503 Service Unavailable** — Syncthing not configured
```json
{
  "status": 503,
  "title": "Syncthing Unavailable",
  "detail": "Syncthing is not configured on the server. Set SYNCTHING_ROOT env var and restart the API server.",
  "instance": "/api/v1/syncthing/folders",
  "error_code": "SYNCTHING_UNAVAILABLE"
}
```

**401 Unauthorized** — Invalid/missing token (standard auth)

---

## 7. UI/UX Specifications

### 7.1 FolderPicker Component

| Property | Specification |
|---|---|
| **Trigger** | Button (variant="outline", full width) showing current path or placeholder |
| **Popover** | `shadcn/ui` Popover, width ~400px, max-height 400px with scroll |
| **Tree View** | Nested `Collapsible` components, indent 1.5rem per level |
| **Folder Icon** | `Folder` from `lucide-react` (closed), `FolderOpen` (expanded) |
| **Selection** | Click folder name to select; highlighted with `bg-primary/10` |
| **Clear Button** | Ghost button in popover footer: "Clear selection" |
| **Keyboard** | Arrow Up/Down navigate, Enter select, Escape close, Left/Right collapse/expand |
| **Loading State** | Skeleton placeholders while fetching |
| **Error State** | Inline error message in popover if fetch fails |

### 7.2 Job Submission Form Changes

- `input` field: Replace `Input` with `FolderPicker`, same label "Input path"
- `output` field: Replace `Input` with `FolderPicker`, same label "Output path"
- Both fields remain optional (per schema)
- YAML preview tab updates live as before (no change)

### 7.3 Responsive Behavior

- Mobile: Popover becomes full-screen sheet (using `Sheet` from shadcn/ui)
- Desktop: Popover anchored to trigger button

---

## 8. Edge Cases and Error Handling

| Scenario | Handling |
|---|---|
| `SYNCTHING_ROOT` not set / not accessible | API returns 503; UI shows "Unable to load folders — Syncthing not configured" in picker |
| Empty folder list (no directories) | Picker shows "No folders found" message |
| Very deep nesting (>10 levels) | UI supports arbitrary depth; consider "..." truncation for display |
| Network error during fetch | Retry button in popover; cached last result shown if available |
| User types path manually | Still allowed — `FolderPicker` is an enhancement, not a replacement for free text |
| Folder deleted after selection | Next fetch will not include it; stale selection remains in form until user changes it |
| Special characters in folder names | Handled by `os.walk` and JSON serialization; UI displays as-is |

---

## 9. Dependencies and Related Issues

| Dependency | Description |
|---|---|
| **Backend**: `syncthing_service.py` | Already provides `SYNCTHING_ROOT` access; new endpoint reuses its configuration |
| **Frontend**: `shadcn/ui` components | `Popover`, `Collapsible`, `Button`, `Label`, `ScrollArea`, `Sheet` (mobile) |
| **Frontend**: `react-hook-form` | `FolderPicker` integrates via `onChange` → `form.setValue()` |
| **Shared**: `syncthing_folders.py` | New schema shared between backend and frontend (via openapi.yaml generation) |

**Related Issues**:
- #105 — Job submission writes to Syncthing folder (this picker reads from it)
- #166 — Syncthing dashboard left panel (shows folder status; picker complements it)
- #181 — Sample calculation program (uses input/output paths; profiles define defaults)

---

## 10. Acceptance Criteria

### Functional

- **AC-1** — `GET /api/v1/syncthing/folders` returns 200 with `{ root, folders[] }` when Syncthing is configured and accessible.
- **AC-2** — The `folders` array contains all directories under `SYNCTHING_ROOT` (recursive), relative to root, sorted alphabetically.
- **AC-3** — Hidden directories (starting with `.`) are excluded from the list.
- **AC-4** — API returns 503 `SYNCTHING_UNAVAILABLE` when `SYNCTHING_ROOT` is not set or the path does not exist.
- **AC-5** — `FolderPicker` component fetches folder list on mount and displays a navigable tree.
- **AC-6** — Selecting a folder in the picker updates the form field (`input` or `output`) with the relative path.
- **AC-7** — The picker works for both `input` and `output` fields independently.
- **AC-8** — "Clear selection" sets the field to empty string.
- **AC-9** — Manual text entry in the field still works (picker is optional enhancement).
- **AC-10** — YAML preview tab reflects the selected folder paths in `paths.input` and `paths.output`.
- **AC-11** — Mobile view uses `Sheet` (full-screen) instead of `Popover`.
- **AC-12** — Keyboard navigation works (arrows, Enter, Escape).
- **AC-13** — Loading and error states are handled gracefully in the UI.

### Quality

- **AC-14** — `ruff check . && ruff format --check . && mypy .` all exit 0 (backend).
- **AC-15** — `cd frontend && npm run lint && npm run typecheck` exit 0.
- **AC-16** — Backend unit tests pass: `pytest backend/tests/unit/test_syncthing_folders.py` (new test file).
- **AC-17** — Frontend unit tests pass: `cd frontend && npm test` (includes `FolderPicker` tests).
- **AC-18** — Integration test: `pytest backend/tests/integration/test_syncthing_folders.py` tests the endpoint with real filesystem.
- **AC-19** — No new `type: ignore` or widening of mypy overrides.
- **AC-20** — `openapi.yaml` is regenerated and includes the new `/syncthing/folders` endpoint (or schema assertion test passes).

---

## 11. Test Scenarios

### Backend Unit Tests (`backend/tests/unit/test_syncthing_folders.py`)

| Test | Description |
|---|---|
| `test_list_folders_success` | Mock `SYNCTHING_ROOT`, create temp dirs, assert response matches |
| `test_list_folders_excludes_hidden` | Create `.hidden` dir, assert not in response |
| `test_list_folders_sorted` | Create dirs `b`, `a`, assert response is `["a", "b"]` |
| `test_list_folders_empty_root` | Empty root returns empty `folders` array |
| `test_list_folders_unavailable` | Unset `SYNCTHING_ROOT`, assert 503 error |
| `test_list_folders_not_exists` | `SYNCTHING_ROOT` points to non-existent path, assert 503 |

### Backend Integration Tests (`backend/tests/integration/test_syncthing_folders.py`)

| Test | Description |
|---|---|
| `test_e2e_folder_listing` | Real `SYNCTHING_ROOT` temp dir, create nested structure, call API, verify response |

### Frontend Unit Tests (`frontend/src/components/FolderPicker.test.tsx`)

| Test | Description |
|---|---|
| `test_renders_trigger_with_placeholder` | No value → shows placeholder |
| `test_renders_trigger_with_value` | Value set → shows path |
| `test_opens_popover_on_click` | Click trigger → popover opens |
| `test_selects_folder` | Click folder in tree → `onChange` called with path |
| `test_clear_selection` | Click clear → `onChange` called with `""` |
| `test_keyboard_navigation` | Arrow keys move selection, Enter selects |
| `test_build_tree_from_flat_list` | Flat array → correct tree structure |
| `test_excludes_hidden_folders` | Folders starting with `.` not in tree |

### Frontend Integration Tests (E2E)

| Test | Description |
|---|---|
| `test_job_submission_folder_picker` | Open `/jobs/new`, pick input folder, pick output folder, verify form values, submit |

---

## 12. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Backend endpoint implemented and tested (AC-1 to AC-4, AC-14, AC-16, AC-18)
- [ ] Frontend `FolderPicker` component implemented and tested (AC-5 to AC-13, AC-15, AC-17)
- [ ] Integration into job submission form complete (AC-6, AC-7, AC-10)
- [ ] `openapi.yaml` updated (or schema assertion passes) (AC-20)
- [ ] All quality gates pass (AC-14, AC-15, AC-19)
- [ ] PM acceptance

---

## 13. Implementation Notes for SWE

### Backend
1. Add `SyncthingFolderList` schema to `shared/schemas/syncthing_folders.py`
2. Export it from `shared/schemas/__init__.py`
3. Add `/folders` endpoint to `backend/api/v1/syncthing.py`
4. Import `os` for `os.walk`
5. Reuse the `syncthing_unavailable` check pattern from `get_syncthing_status`
6. Write unit tests in `backend/tests/unit/test_syncthing_folders.py`
7. Write integration test in `backend/tests/integration/test_syncthing_folders.py`

### Frontend
1. Add `SyncthingFolderList` type to `frontend/src/services/types.ts`
2. Add `getSyncthingFolders` to `ClusterService` interface
3. Implement in `httpService` and `mockService`
4. Create `FolderPicker.tsx` component with tree building logic
5. Export from `frontend/src/components/index.ts` (or import directly)
6. Update `jobs.new.tsx` to use `FolderPicker` for `input` and `output`
7. Write unit tests for `FolderPicker`
8. Ensure `npm run typecheck` passes (no new type errors)

### Shared
- The `SyncthingFolderList` schema in `shared/` ensures type consistency between backend and frontend
- Run `npm run typecheck` in frontend after backend changes to verify OpenAPI-generated types align

---

## 14. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Large folder tree causes slow API response | Low | Medium | `os.walk` is fast; if needed, add pagination/depth limit later |
| `os.walk` hits permission errors | Low | Medium | Wrap in try/except, skip unreadable dirs, log warning |
| Frontend tree building slow for 10k+ folders | Low | Low | Client-side tree build is O(n); lazy loading can be added if needed |
| Mobile UX for tree navigation | Medium | Low | Use `Sheet` component; test on mobile viewport |
| Schema drift between backend/frontend | Low | High | Shared schema in `shared/` + `openapi.yaml` regeneration + typecheck gate |

---

## 15. Future Enhancements (Out of Scope)

- Lazy-loading tree (load children on expand via additional API endpoint with `path` query param)
- Folder creation from the picker (requires write API)
- Folder search/filter in the picker
- Recent/favorite folders
- Drag-and-drop to reorder folders
- Show folder size/file count in tree