You are classifying job listings for a personal fractional-role search.

## Matching criteria

{role_criteria}

## Task

Review every listing in the JSON array below. Judge each one against the
criteria above by actually reasoning about what it says — not by keyword
presence. A listing can mention "contract" without being genuinely
fractional-friendly for this purpose, and can be a strong match even if its
wording doesn't line up neatly with the criteria's phrasing. Do not guess or
infer employer flexibility that isn't stated in the listing.

Only include a listing in your output if it is a genuine match: it
explicitly states fractional/contract/part-time/interim terms AND fits one
of the target roles (senior Technical Program Management, or Agentic AI
Engineer / AI engineering leadership, or a close adjacent). Omit everything
else entirely - you do not need to explain why something isn't a match.

For each match, return: the listing's url (verbatim, unchanged), which role
category it fits ("senior_tpm" or "agentic_ai_engineer"), and a one-sentence
reason.

## Listings

{listings_json}
