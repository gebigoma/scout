"""Sample records in the exact shape each source really returns.

These are trimmed from real captured responses in `data/runs/`, which is
gitignored and therefore unavailable in CI - so the interesting shapes are
reproduced here instead: WWR's "Headquarters/About Us" preamble that buries
the employment terms hundreds of characters down, RemoteOK's legal-notice
first element and its "mention the word GEM" applicant instruction, and
HN's pipe-delimited "Company | Role | Location | Type" comment convention.
"""

# RemoteOK's API returns a legal notice as element 0, with no "position" key.
REMOTEOK_LEGAL_NOTICE = {
    "legal": "By using this API you agree to the terms of service",
}

REMOTEOK_ITEM = {
    "slug": "remote-fractional-tpm-acme-1136038",
    "id": "1136038",
    "epoch": 1785000000,
    "date": "2026-08-02T15:57:11+00:00",
    "company": "Acme Robotics",
    "position": "Fractional Senior Technical Program Manager",
    "tags": ["contract", "part time", "program management"],
    "description": (
        "<p>We are looking for a <strong>fractional</strong> senior TPM to run "
        "our agent platform program.</p><br/>"
        "Please mention the word **GEM** when applying to show you read the "
        "job post."
    ),
    "location": "Worldwide",
    "url": "https://remoteOK.com/remote-jobs/remote-fractional-tpm-acme-1136038",
}

REMOTEOK_FULLTIME_ITEM = {
    "id": "1136039",
    "date": "2026-08-01T09:00:00+00:00",
    "company": "Globex",
    "position": "Staff Frontend Engineer",
    "tags": ["react", "frontend"],
    "description": "<p>Full-time, permanent. Build our web app.</p>",
    "url": "https://remoteOK.com/remote-jobs/remote-staff-frontend-globex-1136039",
}

# The head of this description is pure boilerplate; the employment terms sit
# far past any fixed head-truncation point. This is the case that motivated
# _extract_snippet.
WWR_ITEM = {
    "title": "Cloudflare: Principal Partner Solutions Engineer",
    "link": "https://weworkremotely.com/remote-jobs/cloudflare-principal-partner-se",
    "pubDate": "Wed, 22 Jul 2026 07:00:51 +0000",
    "description": (
        "<p><strong>Headquarters:</strong> Hybrid or Remote</p>"
        "<div class=\"content-intro\"><div><strong>About Us</strong></div>"
        "<p>" + ("We are on a mission to help build a better Internet. " * 12) +
        "</p><p>This engagement is offered on a part-time contract basis of "
        "20 hrs/week.</p><p>Apply with a cover letter.</p></div>"
    ),
}

# No ": " in the title - the company/role split has to fall back cleanly.
WWR_ITEM_NO_COMPANY = {
    "title": "Senior Product Designer",
    "link": "https://weworkremotely.com/remote-jobs/senior-product-designer",
    "pubDate": "Thu, 23 Jul 2026 08:00:00 +0000",
    "description": "<p>Full-time role designing our product.</p>",
}

WWR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely: Remote Programming Jobs</title>
    <item>
      <title>Acme: Fractional AI Engineer</title>
      <link>https://weworkremotely.com/remote-jobs/acme-fractional-ai-engineer</link>
      <pubDate>Wed, 22 Jul 2026 07:00:51 +0000</pubDate>
      <description>&lt;p&gt;Contract, 20 hrs/week.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Globex: Staff Backend Engineer</title>
      <link>https://weworkremotely.com/remote-jobs/globex-staff-backend</link>
      <pubDate>Thu, 23 Jul 2026 09:00:00 +0000</pubDate>
      <description>Full-time only.</description>
    </item>
  </channel>
</rss>
"""

HN_ITEM = {
    "thread_title": "Ask HN: Who is hiring? (August 2026)",
    "id": 49156689,
    "created_at": "2026-08-03T15:00:59.000Z",
    "text": (
        "Snout | Agentic AI Engineer | Remote US | Contract"
        "<p>Join us at Snout on our mission to ensure no one ever has to make "
        "a health decision for their pet based on their bank balance."
        "<p>We&#x27;re hiring on a part-time contract basis to start."
    ),
}

# Header shapes taken from a single real "Who is hiring" thread, with the
# company names kept and the wording trimmed. The point of the list is the
# field ORDER: the "Company | Role | Location | Type" convention holds for
# barely half of them.
HN_HEADERS = [
    # (header, expected company, expected title)
    ("Senzing | Platform Engineer | Remote (USA) | Full-Time",
     "Senzing", "Platform Engineer"),
    ("SmileID | Senior Engineer, ML/AI | REMOTE | Full-Time | Europe",
     "SmileID", "Senior Engineer, ML/AI"),
    # Second field is a bare URL.
    ("Seeq |  https://seeq.com  | Staff/Principal Software Engineer | REMOTE",
     "Seeq", "Staff/Principal Software Engineer"),
    # Second field is the employment type; the roles come third.
    ("PostHog | Full-Time | Technical CSMs, AI Research Engineer | REMOTE",
     "PostHog", "Technical CSMs, AI Research Engineer"),
    # Second field is a salary band.
    ("SmarterDx | 150-250k+ + equity | Remote (US only) | Multiple roles",
     "SmarterDx", "Multiple roles"),
    # Second field is a YC batch.
    ("Ashby | YC 19 | REMOTE | Hiring Engineering Leaders | $200k–$275k",
     "Ashby", "Hiring Engineering Leaders"),
    # No role anywhere in the header - the roles are listed in the body.
    ("Flywheel Motion (flywheelmotion.com) | REMOTE (worldwide) | Contract",
     "Flywheel Motion (flywheelmotion.com)", "REMOTE (worldwide) | Contract"),
]

HN_SEARCH_RESPONSE = {
    "hits": [
        {"objectID": "49156000", "title": "Ask HN: Who is hiring? (August 2026)"},
        {"objectID": "48000000", "title": "Ask HN: Who is hiring? (July 2026)"},
    ]
}

HN_THREAD_RESPONSE = {
    "id": 49156000,
    "children": [
        {"id": 49156689, "created_at": "2026-08-03T15:00:59.000Z",
         "text": "Snout | Agentic AI Engineer | Remote US | Contract"},
        # Deleted comments come back with empty/None text and must be skipped.
        {"id": 49156690, "created_at": "2026-08-03T15:05:00.000Z", "text": "   "},
        {"id": 49156691, "created_at": "2026-08-03T15:10:00.000Z", "text": None},
        {"id": 49156692, "created_at": "2026-08-03T15:15:00.000Z",
         "text": "Globex | Staff Engineer | NYC | Full Time"},
    ],
}


def listing(url, **overrides):
    """A normalized listing, as the stages after normalize expect it."""
    base = {
        "source": "remoteok",
        "title": "Fractional Senior TPM",
        "company": "Acme Robotics",
        "url": url,
        "posted_date": "2026-08-02T15:57:11+00:00",
        "snippet": "Fractional senior TPM, 20 hrs/week.",
        "tags": ["contract"],
    }
    base.update(overrides)
    return base


# --- first-TPM lane fixtures -------------------------------------------

COMPANIES_CSV_SAMPLE = """name,ats,token,headcount,source
Acme Robotics,greenhouse,acmerobotics,85,lightspeed
Bounce Systems,ashby,bouncesystems,,bessemer
Cavil Data,lever,cavildata,140,accel
"""

# Greenhouse's `?content=true` flag is load-bearing - without it there is no
# "content" (description) field at all.
GREENHOUSE_JOB = {
    "id": 4029384756,
    "title": "First Technical Program Manager",
    # first_published is the field normalize.py actually reads for
    # posted_date; updated_at is years newer, so a test asserting on the
    # wrong field would still pass with a bogus recency signal.
    "first_published": "2023-02-14T09:00:00-08:00",
    "updated_at": "2026-08-01T12:00:00-07:00",
    "absolute_url": "https://boards.greenhouse.io/acmerobotics/jobs/4029384756",
    "content": "<p>You will be our <strong>first TPM hire</strong>, establishing "
               "the program management function from scratch.</p>",
    "departments": [{"name": "Research & Development"}],
}

# Ashby's posting-api includes descriptionPlain by default.
ASHBY_JOB = {
    "id": "9f2b1c3d-0000-0000-0000-000000000000",
    "title": "Senior Technical Program Manager",
    "jobUrl": "https://jobs.ashbyhq.com/bouncesystems/senior-tpm",
    "publishedAt": "2026-07-28T00:00:00.000Z",
    "descriptionPlain": "Join us as our second TPM on the team, building out "
                        "our TPM practice alongside engineering leadership.",
}

# Lever's postings endpoint (?mode=json) returns a bare JSON list.
LEVER_JOB = {
    "id": "a1b2c3d4-1111-1111-1111-111111111111",
    "text": "Staff Program Manager, Infrastructure",
    "hostedUrl": "https://jobs.lever.co/cavildata/staff-program-manager",
    "createdAt": 1785200000000,
    "descriptionPlain": "Vulnerability scanning on TPM 2.0 modules and vTPM "
                        "attestation - no program management language here.",
}


def verdict(id_, verdict="match", role_category="senior_tpm", **overrides):
    """A single chunk-response entry, as the model returns it."""
    base = {"id": id_, "verdict": verdict}
    if verdict == "match":
        base.update(role_category=role_category,
                     reason="Explicitly fractional and a senior TPM role.")
    base.update(overrides)
    return base


def match(url, role_category="senior_tpm", **listing_overrides):
    """A classify-stage match: the model's verdict plus the source listing."""
    return {
        "url": url,
        "role_category": role_category,
        "reason": "Explicitly fractional and a senior TPM role.",
        "listing": listing(url, **listing_overrides),
    }


def scored(url, fit_score, role_category="senior_tpm", **listing_overrides):
    """A score-stage record: a match plus its fit score and rationale."""
    m = match(url, role_category, **listing_overrides)
    m["fit_score"] = fit_score
    m["rationale"] = f"Scored {fit_score} on explicit fractional terms."
    return m
