# What counts as a match

A listing is a match if it's **both**:

1. **Fractional-friendly** — explicitly fractional, contract, part-time,
   interim, or advisory. Not a standard full-time role (unless the posting
   explicitly allows contract/fractional arrangements).
2. **Role fit** — one of:
   - Senior Technical Program Manager / TPM (or Director/Head of TPM)
   - Agentic AI Engineer, AI Engineer with agent/agentic-systems focus,
     AI engineering leadership (e.g. Head of AI Engineering)
   - Close adjacents to the above (use judgment — e.g. "Fractional VP Eng
     focused on AI agent platform" fits; "Fractional CMO" does not)

Remote-friendly is a plus but not a hard requirement — note it in the
rationale either way.

## Not a match

- Full-time-only roles with no contract/fractional option
- Unrelated functions (CMO, CFO, sales, marketing, design) even if fractional
- Junior/mid-level titles without the "senior" bar
- Generic "software engineer" postings with no AI/agentic angle

## Fit score guide

Every match gets a 0-100 fit score (not just a yes/no). Rough anchors:

- **85-100** — Ideal: explicit, generous fractional/contract terms (not just
  "will consider"); role is centrally senior TPM or agentic-AI-engineering
  work, not adjacent; remote-friendly.
- **60-84** — Strong: clearly fractional-friendly and clearly role-fit, but
  missing one nice-to-have (e.g. not remote, or role is a close adjacent
  rather than the exact title).
- **35-59** — Marginal: still a genuine match per the criteria, but weaker
  on multiple fronts (e.g. vague contract terms, or role fit leans heavily
  on "close adjacent" judgment rather than a clean title match).
- **0-34** — Shouldn't really appear (if it didn't clear the match bar
  above, it shouldn't reach scoring at all) — treat a low score here as a
  signal to double check the classify step's judgment.

## Output format

For each match in `matches/<date>.md`, include: title, company, source,
link, fit score, and a one-line rationale for why it fits (and, ideally,
why it scored where it did). If a week has zero matches, say so explicitly
rather than omitting the file — that's useful signal too.
