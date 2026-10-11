# Setting up a Claude Project for JobLookup

1. claude.ai → **Projects → Create project** → name it "JobLookup".
2. **Instructions**: paste the contents of `1_PROJECT_INSTRUCTIONS.md` (below the line).
3. **Knowledge / files**: upload `2_PROJECT_MEMORY.md`, `3_CONVERSATION_SUMMARY.md`, and the files
   listed below. If the GitHub integration is available, connect the repo instead of uploading code
   (it stays in sync); otherwise upload the files.
4. Memory: after the first chat, review the project's memory and correct anything off.

Files to add from the repo (docs first, then the code that matters most):
- `README.md`, `CLAUDE.md`, `db/README.md`, `webapp/README.md`
- `config/profile.yaml`, `config/resume.txt`
- `src/jobseeker/config.py` (ENV_VARS registry), `src/jobseeker/matching.py`, `src/jobseeker/pipeline.py`,
  `src/jobseeker/resume_hub.py`, `src/jobseeker/database.py`
- `render.yaml`, `.github/workflows/collect.yml`, `.github/workflows/digest.yml`, `.github/workflows/backfill.yml`

Never upload: `.env` files, the raw chat transcript, screenshots of Render/Supabase settings — they
contain passwords and keys.
