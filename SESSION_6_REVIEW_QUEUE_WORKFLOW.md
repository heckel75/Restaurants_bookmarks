# Session 6 — Review Queue Workflow

**Date:** 2026-06-05

## Saved Google Sheets view

Create and keep this filter view on the `Restaurants` tab:

```text
Review Queue — Needs Review TRUE
```

Filter:

```text
Needs Review = TRUE
```

Sort:

```text
Review Reason A → Z
```

The review queue should never contain blank `Review Reason` values.

## Queue after Session 6 cleanup

Approximate counts after cleanup:

```text
manual_review_required 31
city_mismatch 8
domain_mismatch 181
name_mismatch 414
no_google_candidate 12
missing_location_hint 0
blank Review Reason 0
```

## Manual action rules by review reason

### manual_review_required

Use this when a row has enough information for manual checking or controlled retry, but is not yet validated. Typical examples are rows with `Name + Website`, rows where a better location hint was added, or rows moved out of a more specific mismatch after inspection.

Allowed actions:

- If the exact Google Maps place is manually confirmed, fill `Google Place ID`, set `Match Method = manual_google_place_id` or `manual_verified`, set `Needs Review = FALSE`, and clear `Review Reason`.
- If the row needs script retry, add the best available `Arrondissement`, `Town`, or `City`, keep `Needs Review = TRUE`, and keep `Review Reason = manual_review_required`.
- If it is not a restaurant, set `Status = not_relevant`, set `Needs Review = FALSE`, and clear `Review Reason`.
- If it is a duplicate candidate, do not delete immediately unless certain. Add a note such as `possible duplicate — same name, different website`.

### name_mismatch

Use this when Google found a candidate, but the name did not safely match the row.

Allowed actions:

- If useful, add a better `Arrondissement`, `Town`, `City`, website, or social link, then set `Review Reason = manual_review_required`.
- If not useful or impossible to identify, set `Status = not_relevant` or `archived`, set `Needs Review = FALSE`, and clear `Review Reason`.
- Do not fill `Google Place ID` unless the exact Google Maps place is manually verified.

### domain_mismatch

Use this when the name may match, but the website/domain evidence does not safely match. This bucket is mixed and should not be bulk-resolved.

Allowed actions:

- If Google found the same restaurant and the domain difference is explainable by an old/new website, manually accept the row by filling or confirming `Google Place ID`, setting `Match Method = manual_google_place_id` or `manual_verified`, setting `Needs Review = FALSE`, and clearing `Review Reason`.
- If the row has useful `Name + Website` but no trusted Google fields, set `Review Reason = manual_review_required`, keep `Needs Review = TRUE`, and keep `Status = to_review`.
- If Google found a different restaurant, add a better location hint if possible and set `Review Reason = manual_review_required` or keep a more specific mismatch reason.
- If the original website is not a restaurant, delete the row if it is clearly a bookmark mistake, or set `Status = not_relevant`, set `Needs Review = FALSE`, and clear `Review Reason`.

### city_mismatch

Use this when the Google candidate appears to be in the wrong city, town, or arrondissement. This bucket is mixed and should not be bulk-resolved.

Allowed actions:

- If the location hint in the row is wrong, correct `Arrondissement`, `Town`, or `City`, then set `Review Reason = manual_review_required`.
- If Google found the wrong city but the row is useful, keep `Needs Review = TRUE`, keep or set `Review Reason = city_mismatch`, and add a better hint if known.
- If it is a bookmark mistake, delete it.
- If the restaurant is closed, set `Status = closed`, set `Needs Review = FALSE`, clear `Review Reason`, and leave `Google Place ID` blank unless a verified place ID is known.

### no_google_candidate

Use this when the script could not find a Google Places candidate.

Allowed actions:

- If probably closed or no longer on Google Maps, set `Status = closed`, set `Needs Review = FALSE`, clear `Review Reason`, leave `Google Place ID` blank, and leave `Match Method` blank.
- If it is a bookmark mistake or clearly not a restaurant, delete it.
- If it is still useful and has missing context, add a better location hint or website/social link, then set `Review Reason = manual_review_required` for controlled retry.

### missing_location_hint

Use this when a row has no usable location hint and no useful link.

Allowed actions:

- If it was only a test row, delete it.
- If it is a real restaurant, add `Arrondissement`, `Town`, `City`, website, or social link, then set `Review Reason = manual_review_required`.
- If it cannot be identified, set `Status = not_relevant` or delete it if it is clearly a mistake.

### blank Review Reason

Blank review reasons are not allowed.

Allowed actions:

- If the row has useful `Name + Website`, set `Review Reason = manual_review_required`.
- Otherwise assign the most accurate reason from the controlled list.

## Session 6 log

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
- Documented manual action rules for each review reason.

**Tested:**

Initial review queue counts:

```text
blank: 30
city_mismatch: 9
domain_mismatch: 182
missing_location_hint: 1
name_mismatch: 415
no_google_candidate: 15
```

Final approximate review queue counts:

```text
manual_review_required: 31
city_mismatch: 8
domain_mismatch: 181
name_mismatch: 414
no_google_candidate: 12
missing_location_hint: 0
blank Review Reason: 0
```

**Results:**

- Blank `Review Reason` count is now 0.
- `missing_location_hint` count is now 0 after deleting the test row.
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
