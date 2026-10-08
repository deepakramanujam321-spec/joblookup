# Build context must be the REPO ROOT, not webapp/, since this needs
# src/jobseeker too: docker build -f webapp/Dockerfile .
# Render's "Dockerfile Path" setting does this automatically when "Root
# Directory" is left unset — see webapp/README.md.

FROM python:3.12-slim

WORKDIR /app

COPY webapp/requirements.txt webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt

COPY src/ src/
COPY config/ config/
COPY webapp/ webapp/

# Run from inside webapp/ so `main.py`'s plain `from auth import ...` /
# `from schemas import ...` resolve the same way they do in local dev
# (webapp/README.md: `cd webapp && uvicorn main:app`) -- running as
# `webapp.main:app` from /app instead would need those as relative imports,
# which breaks the local-dev invocation. One import style, one WORKDIR.
WORKDIR /app/webapp

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
