# What counts as a match — first-TPM lane

This lane looks for **full-time** roles where the hire would be the first (or
near-first) Technical Program Manager at a startup, responsible for
establishing the TPM function rather than joining an existing one.

The fractional/contract gate in [`ROLE_CRITERIA.md`](./ROLE_CRITERIA.md) does
**not** apply here. Full-time W2 headcount is the expected shape.

A listing is a match if it's **both**:

1. **Foundation-laying** — the posting itself indicates this person would be
   the first TPM, one of the first, or would establish/build the program
   management function. The evidence must be *in the posting text*. Do not
   infer it from company size, funding stage, or the absence of other TPM
   listings.
2. **Role fit** — Technical Program Manager, Senior/Staff/Principal TPM, or
   Head/Director of Technical Program Management where the posting makes
   clear it is a founding, hands-on role rather than managing an existing
   team.

## What counts as foundation-laying evidence

Strongest (explicit):

- "first technical program manager", "first TPM hire", "our first TPM"
- "our second TPM", "second TPM on the team"
- "establish the program management function"
- "build out our TPM practice"
- "you will define how program management works here"

Also genuine (implicit but unambiguous):

- "there is no one in this role today"
- "you'll be building this function from scratch" / "from the ground up"
- "0 to 1" applied to the program management function itself
- "you will be the connective tissue between engineering and product" *paired
  with* an explicit statement that the role is new

## Not a match

- **An existing TPM org.** "Join our team of TPMs", "report to the Director of
  TPM", "one of our 15 program managers" — the opposite of this thesis, no
  matter how senior the title.
- **Non-technical program/project management.** Marketing programs, PMO
  administration, change management, generic project coordination.
- **Product Manager.** PM is a different function; only match if the posting
  explicitly describes technical *program* management work.
- **Junior/mid scope.** Coordinating standups and taking notes is not
  establishing a function.
- **Generic "0 to 1" language about the product** rather than about the
  program management function. Nearly every startup req says "0 to 1"
  somewhere; it only counts when it modifies the role itself.
- **Foundation language that only appears in boilerplate** — an "About Us"
  section saying the company was "built from scratch" is not evidence about
  this role.

## A specific false positive to reject

**"TPM" also means Trusted Platform Module.** Infrastructure, security, and
hardware postings use the acronym constantly ("TPM 2.0", "TPM-backed
attestation", "vTPM"). These are not program management roles. If "TPM"
appears only in a hardware/security/cryptography context and no program
management language accompanies it, it is not a match — and this lane sources
heavily from infra and dev-tools companies, so expect to see it.

## Fit score guide

Every match gets a 0-100 fit score. Company headcount is a **signal, not a
gate** — the target band is 50-150 employees, but headcount data is stale and
frequently unavailable, so a missing or out-of-band number lowers the score
rather than disqualifying the listing.

- **85-100** — Ideal: explicit first/second-TPM language, company in the
  50-150 band, infrastructure / dev-tools / AI domain, senior scope.
- **60-84** — Strong: explicit foundation language and clear role fit, but
  missing one dimension (headcount unknown, or somewhat outside the band, or
  domain is adjacent rather than infra/dev-tools).
- **35-59** — Marginal: still a genuine match, but the foundation evidence
  leans implicit rather than explicit, or the company is well outside the size
  band (a 400-person company hiring its "first TPM" is real but a different
  job than intended).
- **0-34** — Shouldn't appear. A low score here means the score stage
  disagreed with classify; treat it as a signal to check classify's judgment,
  not as a listing to pursue.

State the company's headcount in the rationale when known, and say "size
unknown" when it isn't — never guess it.

## Output format

For each match in `matches/<date>.md`: title, company, source, link, fit
score, employment type, company size (or "unknown"), and a one-line rationale
that quotes or paraphrases the specific foundation-laying evidence found in
the posting. The quoted evidence is the point — it's what makes a match
auditable, and what makes a false positive obvious at a glance.
