## Agent Instructions for this project

- Keep `frontend/src` as the single source of truth for frontend code.
- Use `*.ts`/`*.tsx` for frontend implementation files.
- Do not keep compiled/bundled JavaScript outputs in `frontend/src` next to TypeScript sources.
- If you need source duplication during migration, remove the old file family (`.js` when replacing `.ts/.tsx`) in the same commit.
- Prefer one canonical implementation per module or component name; avoid maintaining parallel implementations in different languages.
- For generated artifacts, write only to build/output directories (`frontend/dist`, logs, data files).
