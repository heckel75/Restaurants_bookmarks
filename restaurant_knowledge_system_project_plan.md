# Restaurant Knowledge System — Project Plan

**Version:** 2.1  
**Scope update:** Paris + suburbs only, easy manual additions, structured Google Places matching, delivery/takeaway tracking, LLM enrichment, and step-by-step session workflow.

---

## 1. What we are trying to achieve

We are building a private, structured restaurant knowledge system for **Paris and suburbs**.

The goal is to have a clean, searchable, map-ready restaurant database that can be used for:

1. planning meals ahead of time;
2. maintaining a reliable personal restaurant archive;
3. choosing places while out and about;
4. eventually sharing curated selections with friends;
5. adding new restaurants very quickly from phone, tablet, or desktop;
6. cleaning closed, irrelevant, duplicate, or ambiguous restaurants over time;
7. identifying whether a restaurant supports **delivery** or **takeaway**;
8. enriching records with structured tags using an LLM without creating tag chaos.

The target workflow is:

```text
Bookmarks HTML / manual additions / quick-add form
        ↓
Python enrichment script
        ↓
Google Places matching and verification
        ↓
LLM enrichment using controlled vocabularies
        ↓
Google Sheets as source of truth
        ↓
Later: private Google Maps overlay / web app
```

The database should become a **clean personal restaurant operating system**, not just a dump of bookmarks.

---

## 2. Updated scope

### 2.1 In scope

Keep restaurants from:

```text
Paris 01 → Paris 20
Paris Takeaway
Periph / Paris suburbs
manual additions in Paris or suburbs
```

The system should support new restaurants added with minimal information, for example:

```text
Name = "Some Restaurant"
Arrondissement = 11
```

or:

```text
Name = "Some Restaurant"
Town = "Boulogne-Billancourt"
```

or:

```text
Name = "Some Restaurant"
Website or Instagram = "..."
```

The script should then try to find the matching Google Places record.

### 2.2 Out of scope

Dublin restaurants are no longer part of the target dataset.

Decision:

```text
Dublin rows should be deleted from the production sheet after a backup copy exists.
```

The main production sheet should be Paris/suburbs only. No Dublin archive tab is required unless the user later changes this decision.

---

## 3. Current system components

### 3.1 Input sources

Current input sources may include:

1. the original bookmarks HTML file;
2. direct manual rows in Google Sheets;
3. future Google Form submissions;
4. future quick-add UI in a private web app.

The system should support all of these, but not all need to be implemented at once.

### 3.2 Main database

Google Sheets is the source of truth.

The sheet should contain one row per restaurant candidate.

### 3.3 Enrichment script

A Python script reads candidate rows and/or bookmarks, tries to match each restaurant to Google Places, then updates the Google Sheet.

The intended behavior is:

#### If the Google Places match is confident

The script should:

- replace the input/bookmark name with the exact Google Places name;
- fill Google Place ID;
- fill address, city, postal code, arrondissement/town, latitude, longitude;
- fill website if available;
- fill Google business status;
- set `Needs Review = FALSE`;
- clear `Review Reason`;
- set `Match Method = google_places`;
- mark the row as validated.

#### If the Google Places match is not confident

The script should:

- keep the input/bookmark name;
- avoid trusting Google location data;
- set `Needs Review = TRUE`;
- set a clear `Review Reason`;
- leave the row ready for manual review.

### 3.4 Google Places

Google Places is used for:

- restaurant identity verification;
- place ID;
- canonical name;
- address;
- coordinates;
- business status;
- website;
- potentially delivery/takeaway/dine-in attributes, depending on API version, pricing tier, and availability.

The script should avoid wasting API calls by skipping rows that already have:

```text
Needs Review = FALSE
Google Place ID filled
```

### 3.5 LLM enrichment

LLM enrichment is required.

The LLM should not freely invent tags.

It should be used to enrich structured fields from a controlled vocabulary, with evidence when possible.

Likely LLM-enriched fields:

```text
Cuisine
Vibe
Features
Delivery / Takeaway confidence
LLM Confidence
LLM Evidence
LLM Tagged at
LLM Model
```

The LLM should run only after a row is either:

- validated by Google Places; or
- manually accepted.

This avoids enriching obviously wrong matches.

### 3.6 Future map layer

The desired future map is not Google My Maps.

Preferred future direction:

```text
Private web app overlaying Google Maps
```

This would use Google Sheets as the backend data source and display custom restaurant markers on top of Google Maps.

---

## 4. Final Google Sheet structure

### 4.1 Required core fields

Keep these columns:

```text
Name
Google Place ID
Canonical Key
Status
Needs Review
Review Reason
Match Method
Confidence
Address
City
Postal Code
Arrondissement
Town
Latitude
Longitude
Website
Instagram
Facebook
Delivery
Takeaway
Cuisine
Vibe
Features
Favorite
Notes
Last Checked
Source
```

### 4.2 Optional future LLM fields

Keep or add these for LLM enrichment:

```text
LLM Confidence
LLM Evidence
LLM Tagged at
LLM Model
LLM Review Needed
```

### 4.3 Final header order after Session 1

The current production header order should be:

```text
Name
Google Place ID
Canonical Key
Status
Needs Review
Review Reason
Match Method
Confidence
Address
City
Postal Code
Arrondissement
Town
Latitude
Longitude
Website
Instagram
Facebook
Delivery
Takeaway
Cuisine
Vibe
Features
Favorite
Notes
Last Checked
Source
LLM Confidence
LLM Evidence
LLM Tagged at
LLM Model
LLM Review Needed
Map Location
Geocode Cache
```

`Map Location` and `Geocode Cache` should remain at the far right and hidden for now.

### 4.4 Columns that can be ignored or deleted

These came from Airtable or map experiments and are not essential now:

```text
Map Location
Geocode Cache
```

They can be deleted once we are sure they are not used anywhere.

---

## 5. Field meanings

### Name

The canonical restaurant name.

If the row is validated, this should be the exact Google Places name.

If the row needs review, this can remain the cleaned input name.

### Google Place ID

The Google Places unique identifier.

This should only be filled when the match is accepted as valid.

### Canonical Key

Internal deduplication key generated from input name, source URL/domain, and location hint.

This is used by the script to find existing rows.

### Status

Allowed values:

```text
active
closed
to_review
not_relevant
archived
```

### Needs Review

Allowed values:

```text
TRUE
FALSE
```

No blank values should remain.

### Review Reason

Used when `Needs Review = TRUE`.

Suggested values:

```text
no_google_candidate
name_mismatch
city_mismatch
domain_mismatch
weak_match
multiple_possible_matches
missing_location_hint
manual_review_required
```

### Match Method

Used when a row has been matched.

Current expected values:

```text
google_places
manual_google_place_id
manual_verified
```

### Confidence

Allowed values:

```text
high
medium
low
```

### Arrondissement

For Paris rows only.

Examples:

```text
1
11
20
```

### Town

For suburbs / non-Paris rows.

Examples:

```text
Boulogne-Billancourt
Montreuil
Saint-Ouen
Neuilly-sur-Seine
```

### Delivery

Allowed values:

```text
TRUE
FALSE
UNKNOWN
```

### Takeaway

Allowed values:

```text
TRUE
FALSE
UNKNOWN
```

### Cuisine / Vibe / Features

Structured fields that should stay clean.

They should come from a controlled vocabulary, not arbitrary LLM output.

---

## 6. Data quality principles

### Principle 1 — Match or review

Every row should end in one of two states.

#### Validated

```text
Needs Review = FALSE
Google Place ID filled
Match Method filled
```

The row should be trusted.

#### Review needed

```text
Needs Review = TRUE
Review Reason filled
```

The row should not be trusted until manually reviewed or reprocessed.

### Principle 2 — Google Places is trusted only when the match is confident

A Google result should not be accepted just because Google returned something.

The script should evaluate:

- name similarity;
- city/town/arrondissement consistency;
- website/domain consistency when available;
- whether multiple candidates are possible;
- whether the result is actually a restaurant or related food venue.

### Principle 3 — Validated rows should be skipped on reruns

To avoid wasting API calls, the script should skip rows where:

```text
Needs Review = FALSE
Google Place ID is not blank
```

### Principle 4 — Review rows should explain why

A row should not just be marked for review. It should say why.

Example:

```text
Review Reason = city_mismatch
```

### Principle 5 — Do not hide data uncertainty

If the script is unsure, the correct behavior is to mark the row for review.

### Principle 6 — LLM output must be constrained

The LLM should only select from allowed vocabularies.

Invalid tags should be rejected or written to evidence/review fields, not silently added to the main taxonomy.

### Principle 7 — Delivery and takeaway need evidence

Delivery/takeaway should not be guessed without a source.

Possible evidence sources:

- Google Places `delivery` / `takeout` fields when available;
- restaurant website text;
- delivery platform page;
- LLM extraction from website snippets;
- manual confirmation.

If evidence is weak, use:

```text
UNKNOWN
```

---

## 7. Important doubts and pushback

### 7.1 Adding by only name + arrondissement/town is useful but ambiguous

This is a good quick-add workflow, but it can create ambiguous matches.

Example:

```text
Name = "Le Petit Café"
Arrondissement = 11
```

There may be multiple candidates or a similarly named place.

Rule:

```text
If exact confidence is not high enough, mark Needs Review = TRUE.
```

### 7.2 Paris/suburbs-only scope is strongly recommended

Removing Dublin is a good decision.

It simplifies:

- matching logic;
- location hints;
- views;
- future map filters;
- personal daily usage.

Dublin should be deleted from the production sheet after backup, based on the current project decision.

### 7.3 Delivery/takeaway may be expensive or incomplete from Google Places

Google Places has delivery and takeout-related fields in the newer data field list, but their availability and pricing tier need to be considered. We should not assume every restaurant will return reliable delivery/takeaway data.

Practical decision:

```text
Use Google delivery/takeout fields if cost is acceptable.
Otherwise use LLM/website/manual evidence and mark uncertain cases UNKNOWN.
```

### 7.4 LLM is necessary but should come after matching is stable

LLM enrichment is important, but it should not run before the row identity is correct.

Recommended rule:

```text
Only run LLM tagging on validated rows.
```

### 7.5 We should not overbuild the map before the sheet is stable

The map is valuable, but only after:

- matching is reliable;
- review workflow is stable;
- delivery/takeaway fields are defined;
- LLM tagging plan is ready.

---

## 8. Recommended way to proceed

Yes, working in structured 45-minute sessions is a good way to proceed.

Why this is a good approach:

- the project has many moving parts;
- it is easy to lose track of what changed;
- data quality must be checked after each change;
- each session can produce a concrete deliverable;
- logs make it easier to continue in a new chat without losing context.

Recommended chat starter:

```text
Ready to start session X
```

At the end of each session, update:

- what was completed;
- what was tested;
- what remains open;
- what should happen next.

One improvement to this approach:

Keep this Markdown file as the project control document, and after each session append a short log entry under `Session Logs`.

### Step-by-step working rule

At the start of each session, the assistant should first give a short explanation of:

1. the objective of the session;
2. why this session matters;
3. the expected outcome by the end of the session.

After that, the assistant must work **step by step**, not by giving a long list of actions.

Rules for each session:

- Give only **one concrete action at a time**.
- Wait for the user to reply `Done`, paste a result, or ask a question before continuing.
- Do not give 5–10 instructions at once unless the user explicitly asks for a checklist.
- When checking a sheet, script, or file, ask the user to paste only the specific thing needed for the next step.
- After each user reply, verify the result and then provide the next single action.
- If something is wrong, explain the correction clearly and stay on that step until fixed.
- At the end of the session, provide a session log entry that can be pasted into this project Markdown file.
- Keep the session within a practical 45-minute scope and stop at a clean checkpoint.

Preferred interaction style:

```text
Session objective:
<short explanation>

Why this matters:
<short explanation>

Expected result:
<short explanation>

Step 1:
<one action only>

Reply "Done" when completed.
```

---

## 9. 45-minute session plan

Each session should be small enough to finish, test, and log.

---

### Session 1 — Finalize Paris/suburbs sheet schema

**Goal:** make the sheet structure stable for Paris/suburbs only.

**Timebox:** 45 minutes.

**Tasks:**

1. Confirm final required columns exist.
2. Add missing columns:
   - `Town`
   - `Delivery`
   - `Takeaway`
   - `Source`
   - `Favorite`
   - `Notes`
   - `Last Checked`
   - `LLM Review Needed`
3. Decide whether to delete or ignore:
   - `Map Location`
   - `Geocode Cache`
4. Confirm all headers exactly match expected names.
5. Confirm Dublin rows will be deleted after backup, not archived.

**Success criteria:**

- final columns exist;
- no critical column is missing;
- headers are stable for the script;
- Dublin is out of the main production dataset.

**Output:**

- updated Google Sheet structure;
- session log.

---

### Session 2 — Remove Dublin and clean scope

**Goal:** make the dataset Paris/suburbs only.

**Timebox:** 45 minutes.

**Tasks:**

1. Identify Dublin rows.
2. Delete Dublin rows from the production sheet.
3. Confirm no Dublin rows remain.
4. Confirm remaining rows are Paris/suburbs only.
5. Confirm `City`, `Arrondissement`, and `Town` make sense.

**Success criteria:**

- main sheet contains only Paris/suburbs rows;
- no Dublin rows remain in production;
- no archive tab is needed because the project decision is deletion after backup.

**Output:**

- scoped dataset;
- session log.

---

### Session 3 — Clean core status fields

**Goal:** ensure all rows have consistent review/status fields.

**Timebox:** 45 minutes.

**Tasks:**

1. Check blank `Needs Review` values.
2. Ensure `Needs Review` contains only:
   - `TRUE`
   - `FALSE`
3. Ensure `Status` contains only allowed values.
4. Ensure `Confidence` contains only:
   - `high`
   - `medium`
   - `low`
5. Run or adjust the backfill script if needed.

**Success criteria:**

- no blank `Needs Review` cells;
- no invalid `Status` values;
- no invalid `Confidence` values.

**Output:**

- cleaned control fields;
- session log.

---

### Session 4 — Fast manual addition workflow

**Goal:** make adding restaurants easy from iPhone, tablet, or desktop.

**Timebox:** 45 minutes.

**Tasks:**

1. Decide input method:
   - Google Form;
   - direct Google Sheet row;
   - lightweight quick-add tab.
2. Define minimum input fields:
   - `Name`
   - `Arrondissement` OR `Town`
   - optional `Website`
   - optional `Instagram`
   - optional `Notes`
3. Create a quick-add tab or form.
4. Decide how new rows are merged into the main sheet.
5. Confirm the script processes a test row added only with name + arrondissement.

**Success criteria:**

- adding 5–10 restaurants per month is easy;
- new rows can be enriched by the script;
- ambiguous additions go to review.

**Output:**

- working quick-add workflow;
- session log.

---

### Session 5 — Lock the Google Places matching model

**Goal:** make matching behavior deterministic and understandable.

**Timebox:** 45 minutes.

**Tasks:**

1. Review current matching rules.
2. Confirm accepted-match behavior:
   - replace `Name` with Google Places name;
   - update address and coordinates;
   - set `Needs Review = FALSE`;
   - set `Match Method = google_places`.
3. Confirm rejected-match behavior:
   - keep input name;
   - clear untrusted Google fields;
   - set `Needs Review = TRUE`;
   - set `Review Reason`.
4. Test with known cases:
   - Chapter One-type bad match case;
   - a strong Paris match;
   - a suburb match;
   - an Instagram-only link;
   - a manual row with name + arrondissement only.

**Success criteria:**

- clear distinction between validated and review rows;
- obvious matches are accepted;
- ambiguous matches are reviewed;
- accepted rows are normalized to Google Places names.

**Output:**

- final matching rules;
- list of remaining edge cases;
- session log.

---

### Session 6 — Review queue workflow

**Goal:** make manual review efficient.

**Timebox:** 45 minutes.

**Tasks:**

1. Create a filtered view in Google Sheets for:
   - `Needs Review = TRUE`
2. Group or sort by `Review Reason`.
3. Define what to do for each reason:
   - `no_google_candidate`
   - `domain_mismatch`
   - `name_mismatch`
   - `city_mismatch`
   - `missing_location_hint`
4. Decide manual correction workflow:
   - fill Google Place ID manually;
   - add better location hint;
   - mark as not relevant;
   - archive/delete.

**Success criteria:**

- review queue is understandable;
- every review reason has an action;
- manual workflow is clear.

**Output:**

- review process documented;
- session log.

---

### Session 7 — Clean Cuisine / Vibe / Features pollution

**Goal:** remove old Airtable/script pollution from structured tag fields.

**Timebox:** 45 minutes.

**Tasks:**

1. Define temporary allowed values for:
   - `Cuisine`
   - `Vibe`
   - `Features`
2. Search for polluted values such as:
   - `checked`
   - `TRUE`
   - `FALSE`
   - misplaced `bar`
3. Create or run a cleanup script that removes invalid values.
4. Spot-check 20 rows after cleanup.

**Success criteria:**

- no obvious polluted values remain;
- tag fields are clean enough to use;
- uncertain tagging can wait for LLM enrichment.

**Output:**

- cleaned tag fields;
- temporary allowed values;
- session log.

---

### Session 8 — Delivery and takeaway enrichment design

**Goal:** decide how to populate `Delivery` and `Takeaway` reliably.

**Timebox:** 45 minutes.

**Tasks:**

1. Decide allowed values:
   - `TRUE`
   - `FALSE`
   - `UNKNOWN`
2. Check whether Google Places delivery/takeout fields should be used.
3. Estimate cost/pricing impact.
4. Decide fallback sources:
   - website text;
   - LLM extraction;
   - manual confirmation.
5. Test on 10 restaurants.

**Success criteria:**

- clear delivery/takeaway strategy;
- uncertain cases do not become false certainty.

**Output:**

- delivery/takeaway policy;
- session log.

---

### Session 9 — LLM tagging design

**Goal:** design structured auto-tagging without creating a mess.

**Timebox:** 45 minutes.

**Tasks:**

1. Define controlled vocabularies for:
   - `Cuisine`
   - `Vibe`
   - `Features`
2. Decide LLM provider:
   - OpenAI;
   - Gemini;
   - local model.
3. Decide when LLM should run:
   - only validated rows;
   - only empty tag fields;
   - only rows with website or Google Places text.
4. Define output format:
   - tags;
   - confidence;
   - evidence;
   - uncertainty flag.

**Success criteria:**

- clear LLM tagging plan;
- no uncontrolled tags;
- no immediate implementation until vocabulary is stable.

**Output:**

- tag taxonomy draft;
- LLM policy;
- session log.

---

### Session 10 — Implement LLM tagging MVP

**Goal:** add cheap, structured LLM tagging.

**Timebox:** 45 minutes.

**Tasks:**

1. Implement LLM call for one row or small batch.
2. Force output to allowed vocabularies.
3. Store:
   - `Cuisine`
   - `Vibe`
   - `Features`
   - `Delivery`
   - `Takeaway`
   - `LLM Confidence`
   - `LLM Evidence`
   - `LLM Tagged at`
   - `LLM Model`
   - `LLM Review Needed`
4. Test on 10 validated rows.

**Success criteria:**

- LLM tags are controlled;
- no invalid values are written;
- results are useful enough to continue.

**Output:**

- LLM tagging MVP;
- session log.

---

### Session 11 — Improve matching quality

**Goal:** reduce false positives and false negatives.

**Timebox:** 45 minutes.

**Tasks:**

1. Add multiple Google candidate evaluation instead of only first result.
2. Score candidates using:
   - name similarity;
   - city/town/arrondissement match;
   - domain match;
   - address/postal hints.
3. Store best candidate reason.
4. Re-test known difficult cases.

**Success criteria:**

- fewer wrong accepted matches;
- fewer obvious matches incorrectly sent to review.

**Output:**

- improved matching logic;
- session log.

---

### Session 12 — Private map app planning

**Goal:** prepare for the private Google Maps overlay app.

**Timebox:** 45 minutes.

**Tasks:**

1. Confirm app requirements:
   - private use;
   - Google Maps base map;
   - markers from Google Sheet;
   - filters;
   - mobile usable.
2. Decide app stack:
   - Next.js;
   - Google Maps JavaScript API;
   - Google Sheet read API.
3. Define MVP:
   - map;
   - markers;
   - sidebar;
   - filters.
4. Decide deployment approach:
   - local only;
   - Vercel private;
   - password protected.

**Success criteria:**

- clear app scope;
- no code until data model is stable.

**Output:**

- app MVP spec;
- session log.

---

## 10. Session logs

Use this section to keep the project history.

### 10.1 Session log template

Copy this after each future session.

```markdown
### Session X — <title>

**Date:** YYYY-MM-DD

**Goal:**

**Completed:**
- 

**Tested:**
- 

**Results:**
- 

**Issues found:**
- 

**Decisions made:**
- 

**Open questions added/updated:**
- 

**Next recommended session:**
- 
```

---

### 10.2 Session 1 — Finalize Paris/suburbs sheet schema

**Date:** 2026-05-22

**Goal:**
Finalize the Google Sheet schema for the Paris/suburbs restaurant database.

**Completed:**
- Compared the existing header row against the target schema.
- Added missing columns:
  - Town
  - Delivery
  - Takeaway
  - Favorite
  - Notes
  - Last Checked
  - Source
  - LLM Review Needed
- Reordered columns so the schema is stable.
- Moved `Review Reason` and `Match Method` next to the review/status fields.
- Kept `Map Location` and `Geocode Cache` hidden instead of deleting them.
- Added dropdown validation for:
  - Status
  - Needs Review
  - Confidence
  - Delivery
  - Takeaway
  - Favorite
  - LLM Review Needed
- Created a backup copy before deleting Dublin rows.

**Tested:**
- Header row was copied and checked after the column changes.
- Dropdown ranges were applied from row 2 downward to avoid affecting headers.

**Results:**
- The production schema is now ready for Paris/suburbs-only cleanup.
- The sheet is prepared for Google Places matching, delivery/takeaway tracking, and future LLM enrichment.

**Issues found:**
- `Map Location` and `Geocode Cache` may be legacy columns; they are hidden for now.
- Dublin rows still need to be deleted from the production sheet.

**Decisions made:**
- Dublin rows will be deleted instead of archived.
- No `Archive — Dublin` tab is needed.
- LLM fields are kept in the schema now, even though LLM enrichment will come later.
- Future sessions should progress step by step after a short explanation of the objective.

**Open questions added/updated:**
- Confirm later whether `Map Location` and `Geocode Cache` can be safely deleted.
- Confirm how to protect `Favorite` and `Notes` from script overwrites.

**Next recommended session:**
- Session 2 — Remove Dublin and clean scope.

### 10.3 Session 2 — Remove Dublin and clean scope

**Date:** 2026-05-25

**Goal:**
Make the production restaurant dataset Paris/suburbs only by confirming Dublin rows are removed and checking for obvious out-of-scope records.

**Completed:**
- Confirmed Dublin restaurants had already been deleted from the production sheet.
- Searched the full sheet for `Dublin`; no remaining results were found.
- Checked the `City` column for unusual values.
- Found no unusual city values, only some empty cells.
- Checked rows where `City`, `Arrondissement`, and `Town` are all empty.
- Found approximately 651 rows with no usable location fields.
- Confirmed those rows also have no `Postal Code`, `Address`, or `Source`.
- Confirmed most of those rows still have at least one useful bookmark-derived field such as `Website`, `Instagram`, or `Facebook`.
- Searched visible location-blind rows for obvious out-of-scope terms such as Dublin, Ireland, London, Berlin, Madrid, Barcelona, Milan, Rome, and New York.
- Found no obvious out-of-scope records.

**Tested:**
- Full-sheet search for Dublin.
- City filter review.
- Location completeness check using `City`, `Arrondissement`, `Town`, `Postal Code`, `Address`, and `Source`.
- Quick out-of-scope keyword search on the 651 location-blind rows.

**Results:**
- The production sheet is now scoped to Paris/suburbs.
- Dublin cleanup is complete.
- The main remaining data quality issue is not scope, but missing location hints on approximately 651 bookmark-derived rows.

**Issues found:**
- Around 651 rows have no `City`, `Arrondissement`, `Town`, `Postal Code`, `Address`, or `Source`.
- `Source` is empty for those rows, so it cannot help recover the original bookmark folder/location context.
- These rows should not be deleted because they mostly still contain useful website/social links.

**Decisions made:**
- Keep the 651 location-blind rows for now.
- Do not manually fix all 651 rows during this session.
- Future matching logic should treat rows with no location hints carefully.
- Rows that cannot be confidently matched should become:
  - `Needs Review = TRUE`
  - `Review Reason = missing_location_hint`

**Open questions added/updated:**
- Can any location hints be recovered later from bookmark folder names, URLs, website content, Instagram bios, or Google Places search?
- Should the script automatically search Google Places using only name + website/social link when no arrondissement or town exists?
- Should location-blind rows be processed in a separate batch to control false matches?

**Next recommended session:**
- Session 3 — Clean core status fields.

### 10.4 Session 3 — Clean core status fields

**Date:** 2026-05-26

**Goal:**
Ensure all rows have consistent core review/status fields before future matching and enrichment work.

**Completed:**
- Checked `Needs Review` for blank values.
- Confirmed `Needs Review` contains only:
  - TRUE
  - FALSE
- Checked `Status` values.
- Confirmed `Status` contains only allowed values:
  - active
  - closed
  - to_review
  - not_relevant
  - archived
- Checked `Confidence` values.
- Confirmed `Confidence` contains only allowed values:
  - high
  - medium
  - low

**Tested:**
- Used Google Sheets filters to inspect distinct values in:
  - Needs Review
  - Status
  - Confidence

**Results:**
- Core control fields are clean.
- No blank `Needs Review` values remain.
- No invalid `Status` values were found.
- No invalid `Confidence` values were found.
- No backfill script was needed during this session.

**Issues found:**
- None.

**Decisions made:**
- Session 3 can be considered complete.
- The sheet is ready for the next workflow step.

**Open questions added/updated:**
- None.

**Next recommended session:**
- Session 4 — Fast manual addition workflow.

### 10.5 Session 4 — Fast manual addition workflow

**Date:** 2026-05-27

**Goal:**
Create a simple workflow for adding new restaurants quickly from iPhone, tablet, or desktop.

**Completed:**
- Created a new `Quick Add` tab.
- Added the quick-add headers:
  - Name
  - Arrondissement
  - Town
  - Website
  - Instagram
  - Facebook
  - Notes
  - Added At
  - Processed
- Added validation for `Processed` with allowed values:
  - TRUE
  - FALSE
- Added validation for `Arrondissement` with allowed values:
  - 1 through 20
- Added default `Processed = FALSE` values for new rows.
- Confirmed the main production tab is named `Restaurants`.
- Added an Apps Script function called `mergeQuickAddRows`.
- Added a custom Google Sheets menu:
  - Restaurant Tools → Merge Quick Add rows
- Tested merging one quick-add row into the `Restaurants` tab.

**Tested:**
- Added a test row with only:
  - Name
  - Arrondissement
  - Notes
  - Processed = FALSE
- Ran the merge script successfully.
- Confirmed the row was appended to `Restaurants`.
- Confirmed the new row received:
  - Status = to_review
  - Needs Review = TRUE
  - Review Reason = manual_review_required
  - Confidence = low
  - Delivery = UNKNOWN
  - Takeaway = UNKNOWN
  - Source = quick_add
- Ran the menu action again and confirmed already processed rows are not duplicated.

**Results:**
- Fast manual additions are now possible through the `Quick Add` tab.
- New rows can be safely staged before being merged into the main restaurant database.
- Processed rows are marked `TRUE`, preventing accidental duplicate imports.
- The workflow is ready for future Google Places matching.

**Issues found:**
- None during the test.
- Current merge script does not yet check whether a restaurant already exists in `Restaurants`; duplicate detection should be handled later by the matching/deduplication logic.

**Decisions made:**
- Manual additions will use a separate `Quick Add` tab rather than being entered directly into `Restaurants`.
- Minimum useful input is:
  - Name
  - Arrondissement OR Town
- Optional useful fields are:
  - Website
  - Instagram
  - Facebook
  - Notes
- New quick-add rows are imported as review rows first, not trusted rows.

**Open questions added/updated:**
- Should the merge script later check for likely duplicates before appending?
- Should `Quick Add` eventually be connected to a Google Form for easier phone input?
- Should rows added with Website or Instagram be treated with higher initial confidence than name-only rows?

**Next recommended session:**
- Session 5 — Lock the Google Places matching model.

### 10.6 Session 5 — Lock the Google Places matching model

**Date:** 2026-06-03

**Goal:**
Make Google Places matching behavior deterministic and safe for accepted, rejected, quick-add, suburb, and Instagram-only rows.

**Completed:**
- Reviewed the current `enrich_bookmarks.py` matching logic.
- Fixed the typo `Match Methos` → `Match Method`.
- Added support for processing eligible rows already in the `Restaurants` sheet.
- Added support for merged `Quick Add` rows with `Source = quick_add` or `Review Reason = manual_review_required`.
- Added `target_row_num` support so quick-add rows are updated in place instead of duplicated.
- Added a default safety mode:
  - `PROCESS_BOOKMARKS=false`
  - bookmarks are not processed unless explicitly enabled.
- Added `MAX_CANDIDATES` so test runs can process only one candidate.
- Added `missing_location_hint` handling for name-only rows without arrondissement, town, city, website, Instagram, or Facebook.
- Prevented rows already marked with reasons like `missing_location_hint`, `name_mismatch`, `city_mismatch`, or `no_google_candidate` from being retried every run.
- Improved suburb handling using `Town`.
- Improved Paris arrondissement matching using postal-code consistency.
- Improved website cleanup by removing tracking parameters such as `utm_*`, `tracking`, `fbclid`, and `gclid`.

**Tested:**
- Ran `python -m py_compile enrich_bookmarks.py` successfully after changes.
- Tested a strong Paris quick-add match:
  - `Gloria Osteria`, arrondissement 7.
- Tested a name-only unsafe row:
  - `Chapter One`, no location hint and no link.
- Tested a suburb quick-add match:
  - `Polpo`, town `Levallois-Perret`.
- Tested an Instagram-only row:
  - `barbo`, arrondissement 14, Instagram URL.

**Results:**
- `Gloria Osteria Paris 7` was accepted correctly:
  - `Needs Review = FALSE`
  - `Match Method = google_places`
  - `Confidence = high`
  - address, postal code, arrondissement, and Google Place ID filled.
- `Chapter One` was safely rejected:
  - `Needs Review = TRUE`
  - `Review Reason = missing_location_hint`
  - Google Place ID remained blank.
- `Polpo / Levallois-Perret` was accepted correctly as a suburb match.
- Instagram-only matching worked correctly and preserved the Instagram field.
- The script can now safely process one quick-add candidate at a time during testing.

**Issues found:**
- The original script only processed `Restaurants.html`, not rows already merged from `Quick Add`.
- The original script had a typo in `SCRIPT_OWNED_FIELDS`: `Match Methos`.
- Website URLs from Google Places could include tracking parameters.
- Rows already reviewed with `missing_location_hint` could have been retried every run before the safety fix.

**Decisions made:**
- Default script behavior should not process the full bookmarks file.
- Quick-add rows should be processed first in controlled test runs.
- Rows without location hints and without useful links should not call Google Places.
- Uncertain rows should keep Google Place ID blank and remain in the review queue.
- Accepted Google matches should write trusted Google Places data and clear the review reason.

**Open questions added/updated:**
- Should full bookmark reprocessing be enabled later with `PROCESS_BOOKMARKS=true`, or should bookmarks be retired as an input source?
- Should multiple Google candidates be evaluated instead of only the first returned candidate?
- Should quick-add rows with only a website or Instagram but no arrondissement/town be allowed to call Google Places, or should they also require a location hint?
- Should `Last Checked` be filled automatically during Google Places matching?
- Should `Delivery` and `Takeaway` stay untouched until Session 8?

**Next recommended session:**
- Session 6 — Review queue workflow.

### 10.7 Session 6 — Review queue workflow

**Date:** 2026-06-05

**Goal:**
Make the manual review queue understandable and define clear actions for each `Review Reason`.

**Completed:**
- Created and saved a Google Sheets filter view named `Review Queue — Needs Review TRUE`.
- Filtered the view to `Needs Review = TRUE`.
- Sorted the view by `Review Reason`.
- Counted the review queue by reason.
- Found and fixed 30 blank `Review Reason` rows by setting them to `manual_review_required`.
- Confirmed the blank rows mostly had `Name + Website`, so they were useful manual-review candidates rather than pure missing-location cases.
- Found one possible duplicate with the same name and different websites and added notes to both rows.
- Reviewed samples from `name_mismatch`, `domain_mismatch`, `no_google_candidate`, and `city_mismatch`.
- Deleted two rows that were bookmark mistakes and not restaurant records.
- Marked one `no_google_candidate` row as closed.
- Marked one `city_mismatch` row as closed.
- Deleted the `Chapter One` test row used in Session 5.
- Documented manual action rules for each review reason in `SESSION_6_REVIEW_QUEUE_WORKFLOW.md`.

**Tested:**
- Checked initial review queue counts:
  - blank: 30
  - city_mismatch: 9
  - domain_mismatch: 182
  - missing_location_hint: 1
  - name_mismatch: 415
  - no_google_candidate: 15
- Rechecked the queue after cleanup.

**Results:**
- Blank `Review Reason` count is now 0.
- `missing_location_hint` count is now 0 after deleting the test row.
- Current approximate review queue shape is:
  - manual_review_required: 31
  - city_mismatch: 8
  - domain_mismatch: 181
  - name_mismatch: 414
  - no_google_candidate: 12
- The review queue is now organized enough to process reason by reason.

**Issues found:**
- Many `name_mismatch` rows have weak identity/location data and should not be bulk-accepted.
- `domain_mismatch` is mixed; some rows may be valid restaurants with old/new websites, while others may be wrong matches or non-restaurant links.
- `city_mismatch` is mixed and requires row-by-row decisions.
- Some bookmark mistakes are still possible inside the review queue.

**Decisions made:**
- Keep a saved filter view called `Review Queue — Needs Review TRUE`.
- Review rows should never have a blank `Review Reason`.
- Use `manual_review_required` for rows with useful `Name + Website` or rows where a better location hint has been added.
- Do not bulk-resolve `name_mismatch`, `domain_mismatch`, or `city_mismatch`.
- Real closed restaurants can be kept with `Status = closed`, `Needs Review = FALSE`, blank `Review Reason`, and blank `Google Place ID` if no verified Google Places record exists.
- Clear bookmark mistakes can be deleted instead of kept as `not_relevant`.

**Open questions added/updated:**
- Should a small helper script generate review queue counts by `Review Reason` automatically?
- Should the next matching improvement evaluate multiple Google candidates before sending rows to `name_mismatch` or `domain_mismatch`?
- Should old/new website domains be stored as evidence somewhere before manually accepting `domain_mismatch` rows?

**Next recommended session:**
- Session 7 — Clean Cuisine / Vibe / Features pollution.

---

## 11. Open questions

These should be updated as the project evolves.

### Current open questions

1. What is the final allowed vocabulary for `Cuisine`, `Vibe`, and `Features`?
2. Should `Delivery` and `Takeaway` be populated from Google Places fields, LLM website extraction, manual confirmation, or a combination?
3. Is the cost of Google Places delivery/takeout fields acceptable?
4. Should manual additions go into the main sheet directly or into a separate `Quick Add` tab?
5. For name + arrondissement additions, what confidence threshold is acceptable before auto-validating?
6. Should rows with `Needs Review = TRUE` be retried automatically every run, or only when a field changes?
7. Should closed restaurants be archived, hidden, or kept with `Status = closed`?
8. Should LLM tagging run only on validated restaurants?
9. Should Instagram-only links be handled by Google Places search only, or should we add a special resolver?
10. Should the future map app be local-only or deployed privately online?
11. How should favorites and notes be protected from script overwrites?
12. Can `Map Location` and `Geocode Cache` be safely deleted once script dependencies are checked?

---

## 12. How to start future chats

Use this format:

```text
Ready to start session X
```

Example:

```text
Ready to start session 4
```

The assistant should then:

1. read the session objective;
2. give a short explanation of the objective, why it matters, and the expected outcome;
3. give only one concrete action at a time;
4. wait for the user to reply `Done`, paste a result, or ask a question before continuing;
5. verify each completed step before giving the next one;
6. avoid giving 5–10 actions at once unless the user explicitly asks for a checklist;
7. stop after a testable milestone;
8. produce a session log entry at the end;
9. update open questions if needed.

The default session pattern should be:

```text
Session objective:
<short explanation>

Why this matters:
<short explanation>

Expected result:
<short explanation>

Step 1:
<one action only>

Reply "Done" when completed.
```

---

## 13. Current recommended next session

Recommended next session:

```text
Session 2 — Remove Dublin and clean scope
```

Reason:

Session 1 stabilized the sheet schema. The next priority is to make the production dataset Paris/suburbs only by deleting Dublin rows from the backed-up sheet, then checking that `City`, `Arrondissement`, and `Town` are coherent.
