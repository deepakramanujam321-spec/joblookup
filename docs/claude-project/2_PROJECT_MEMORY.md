# JobLookup — Memory

(Add these as Project memory / knowledge. One fact per line so they're easy to edit.)

## About me and what I want
- I'm Deepak Vaduganambi, Backend Engineer (AI Developer) at Oraczen, Bengaluru, since Dec 2024. Strongest in Python/FastAPI, AI/LLM platform infrastructure, distributed systems, RBAC, observability.
- Target roles: Backend / Platform / AI-platform / LLM-infrastructure engineering.
- Location priority: Remote (India) > Hyderabad > Bengaluru > Remote (global, India-friendly) > other Indian metros.
- Minimum pay: 18 LPA. A stated salary below it caps a job's fit at 40; jobs that don't state salary are kept and labelled "Salary not stated".
- Exclusions: Oraczen (current employer) and staffing/recruitment agencies.
- Digest email goes to deepakrama321@gmail.com every Saturday; I review and apply on weekends. Nothing is auto-sent.
- My resumes live in the Drive folder https://drive.google.com/drive/folders/1RTE9C8BJOxQBT0GPVkiOyix4FjbTUI88 (shared "anyone with the link"); default resume = Deepak_Vaduganambi_Resume_Master_Draft.docx.

## Preferences for how Claude works with me
- Ask before spending lots of tokens; think like a senior engineer; use design patterns and best practices.
- Don't add duplicate or semantically duplicate env vars; reuse existing ones.
- Keep Alembic migrations as a clean linear chain; never touch the shifu/gym data in `public`.
- Everything must stay on free tiers.
- I use OpenAI; I don't care about Anthropic as the LLM provider — keep it provider-agnostic (LiteLLM).

## Infrastructure facts
- Repo: github.com/deepakramanujam321-spec/joblookup, branch `main` (feature branch `claude/vigilant-rubin-82o4dn` kept in sync).
- Supabase project tssyakhdwewofhzcmxdj (region ap-northeast-1), schema `jobseeker`, Alembic head `0005_document_blobs`.
- DATABASE_URL = Supabase Session pooler URI (port 5432); any `postgresql+<driver>://` prefix works.
- Render service joblookup-dashboard (free, Docker), health check `/api/health` reports not_configured / auth_failed / host_not_found / unreachable.
- GitHub secrets in use: DATABASE_URL, BRAVE_API_KEY, OPENAI_API_KEY, GMAIL_ADDRESS/GMAIL_APP_PASSWORD (or SMTP_*), optional DASHBOARD_URL, LLM_MODEL.
- Render env: DATABASE_URL, DASHBOARD_USERNAME, DASHBOARD_PASSWORD, OPENAI_API_KEY, TOKEN_ENCRYPTION_KEY (+ optional GOOGLE_* for Gmail/Drive OAuth).
- Claude's sandbox can't reach Supabase, Render, Drive or job boards directly; live checks go through GitHub Actions, the Supabase MCP, or me.

## Open items (as of 2026-10-09)
- Decide whether to exclude Jobgether (a job marketplace that reposts others' jobs; it ranks #1).
- Delete "Resume Template.docx" (blank) and "120824.pdf" (scanned, no text) and one duplicate Master Draft in the Profile page.
- Rotate the Supabase DB password (it appeared in chat/screenshots) and update DATABASE_URL in Render + GitHub.
- Redeploy Render to the latest commit after each push (Manual Deploy → Deploy latest commit), or switch its branch to `main`.
- Google OAuth (Gmail drafts, Drive picker) not configured yet — README "Google integrations".
- Known debt: GMAIL_ADDRESS/GMAIL_APP_PASSWORD duplicate SMTP_USERNAME/SMTP_PASSWORD; collapse once we confirm which pair the GitHub secrets use.
- Not built: detecting recruiter replies in Gmail (needs a read scope I chose not to request).
- GitHub warns Node 20 is deprecated for actions/checkout@v4 / setup-python@v5 (warning only).
