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

## A second verdict: unconfirmed

A listing that is **clearly a senior technical program management role at a
target-profile company** but whose posting says nothing either way about
whether the function already exists is `tpm_unconfirmed`, not a match and not
a rejection.

This tier exists because of what the evidence rule above costs. Over the eval
corpus, 14 listings had "Technical Program Manager" or "TPM" in the title;
twelve were dropped before classify ever saw them, and the two that survived
— Supabase and Baseten — are the only first-TPM matches ever published, both
because their ads happened to say "first" or "Founding". The twelve were TPM
openings at Chainguard, Deepgram, Cribl, Together AI, Sardine, Anyscale and
Baseten: this lane's exact target profile, invisible because of how the ad was
worded rather than what the job was.

Use `tpm_unconfirmed` when **all** of these hold:

- Role fit is clear: the title or body describes technical program management
  work at senior scope.
- The posting gives no foundation evidence of the kind listed above — it
  neither claims the role is new nor indicates an existing TPM org.
- Nothing in the "Not a match" list applies.

**Everything in "Not a match" still rejects.** An existing TPM org, a
non-technical program/project role, a Product Manager req, junior scope, or
Trusted Platform Module is `no_match` — not unconfirmed. Unconfirmed is for
*absent* evidence, never for *contrary* evidence. A posting that says "join
our team of TPMs" has answered the question; it is not unconfirmed, it is a
rejection.

Do not use `tpm_unconfirmed` to hedge a listing that would otherwise be a
match. If the foundation evidence is there, it is a match — say so and quote
it. The two verdicts are distinguished by what the posting contains, not by
how confident you feel.

Score `tpm_unconfirmed` listings on role fit and company profile alone, and
say plainly in the rationale that foundation status is unstated. They are
published in their own section, so the reader already knows the evidence is
missing; the rationale's job is to say whether the *role* is worth a look.

## A specific false positive to reject

**"TPM" also means Trusted Platform Module.** Infrastructure, security, and
hardware postings use the acronym constantly ("TPM 2.0", "TPM-backed
attestation", "vTPM"). These are not program management roles. If "TPM"
appears only in a hardware/security/cryptography context and no program
management language accompanies it, it is not a match — and this lane sources
heavily from infra and dev-tools companies, so expect to see it.

## Fit score guide

Every match gets a 0-100 fit score, on the strength of its foundation evidence
and its role fit.

- **85-100** — Ideal: explicit first/second-TPM language, senior scope, and
  the posting makes the shape of the role unambiguous.
- **60-84** — Strong: explicit foundation language and clear role fit, but
  missing one dimension (scope is senior but the reporting line is unstated,
  or the foundation claim is made once in passing).
- **35-59** — Marginal: still a genuine match, but the foundation evidence
  leans implicit rather than explicit.
- **0-34** — Shouldn't appear. A low score here means the score stage
  disagreed with classify; treat it as a signal to check classify's judgment,
  not as a listing to pursue.

An `tpm_unconfirmed` listing is scored on the same scale, for role fit and
company profile only. Missing foundation evidence is what defines the tier, so
do **not** also penalise the score for it — that would push every unconfirmed
listing under the floor and into "Rejected on scoring", which is a different
claim (the pipeline disagreeing with itself) and would quietly empty the
section this tier exists to fill. A clean senior TPM req at a target-profile
company belongs in the 60-84 band on role fit alone.

**Company size is not scored.** State it in the rationale when the *posting*
states it (team size, company size, "we are ~60 people"), and say nothing when
it doesn't — never guess, and never infer it from funding stage or notability.

Headcount was a scored dimension until 2026-10-05, read from a `headcount`
column in `data/companies.csv`. That column was populated for 0 of 104
companies for its entire life, while this guide lowered the score for a
missing value — so every match was charged for data that never existed. Both
first-TPM matches ever published (Supabase 75, Baseten 72) say "company size
is unknown" in their rationale and were capped for it. That is the same defect
as the domain rule below, found a second time: a dimension that moves rank
rather than filtering, whose cost is invisible because nothing records what
the penalty cost. The column is gone; size survives as an observation.

A 400-person company hiring its "first TPM" is still a different job than
intended — but that is now a judgment made from the posting, where the
evidence actually is, rather than from a column nobody filled.

**Domain is not scored.** State the company's domain in the rationale
(infrastructure, dev-tools, fintech, health, …) so it's visible at a glance,
but do not raise or lower the score for it. An earlier version of this guide
required an infra/dev-tools/AI domain for the 85-100 band, which capped
otherwise-ideal matches at 84 on domain alone. That was a preference encoded
as a rubric, and because it moved rank rather than filtering, its cost would
have been invisible — non-infra matches would simply have sorted lower with
no indication of what was lost. Revisit only with real data on which domains
actually yield founding-TPM roles.

## Output format

For each match in `matches/<date>.md`: title, company, source, link, fit
score, employment type, company size if the posting states it, and a
one-line rationale
that quotes or paraphrases the specific foundation-laying evidence found in
the posting. The quoted evidence is the point — it's what makes a match
auditable, and what makes a false positive obvious at a glance.
