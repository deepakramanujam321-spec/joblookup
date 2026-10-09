
from jobseeker import ingest, profile
from jobseeker.models import JobListing


def test_ingest_and_profile_seed(engine):
    with engine.begin() as conn:
        stats = ingest.upsert_listings(conn, [JobListing(source="lever", url="https://jobs.lever.co/acme/1b2c3d4e-0000-0000-0000-000000000000", title="Senior Backend Engineer", company="Acme", description="Python FastAPI PostgreSQL 5+ years of experience", posted_at="1726000000000", posted_at_evidence="lever_api.createdAt")])
        assert stats.new == 1
        rec = profile.get_or_seed(conn)
        assert rec["data"]["preferred_titles"]
