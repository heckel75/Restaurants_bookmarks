# Restaurant Map App MVP Spec

## 1. Purpose

A private, mobile-friendly restaurant map app for the user-curated Paris MVP dataset.

Google Sheets remains the editing source of truth. The app uses an explicit, deterministic static JSON export as its runtime input and does not read Google Sheets live.

Session 14 delivered the base map and restaurant-selection experience. Search, list navigation, and arrondissement filtering are the next unimplemented MVP layer.

## 2. Repository and deployment status

Current application location:

```text
map-app/
```

The app lives inside the existing restaurant-knowledge-system repository.

Session 14 was implemented and verified locally. The intended app remains private, but the deployment platform, production authentication approach, production map ID, and map style have not been selected or implemented yet.

## 3. App stack

Implemented stack:

- Next.js 16.3.0 App Router;
- TypeScript;
- ESLint;
- npm;
- React 19;
- Google Maps JavaScript API;
- `@googlemaps/js-api-loader` using `setOptions()` and `importLibrary()`;
- `AdvancedMarkerElement`;
- `@googlemaps/markerclusterer`;
- responsive CSS without an additional UI framework.

Google Maps is loaded only in a client component. Loader and initialization state are reused so React rerenders and development Strict Mode do not create duplicate loaders or maps.

## 4. Data source and privacy

Runtime data path:

```text
Private Google Sheet (editing source of truth)
        ↓
explicit export_map_mvp_json.py command
        ↓
data/map_mvp/restaurants.json
        ↓
server-side import and validation
        ↓
approved RestaurantMapItem[] passed to the client map component
```

Rules:

- `data/map_mvp/restaurants.json` is the canonical generated runtime dataset.
- The app does not maintain a manual duplicate.
- The app validates the full public contract server-side before rendering.
- Only the approved 19 public fields are passed to the browser.
- `data/map_mvp/metadata.json` is not part of the browser restaurant contract.
- The app makes no Google Sheets, Places, geocoding, or other restaurant-data runtime calls.
- Internal Sheet, review, LLM, cache, selection-workflow, and export-workflow fields must not be sent to the browser.

Browser configuration:

- `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` supplies the browser-restricted Maps JavaScript API key.
- `NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID` supplies the map ID.
- `DEMO_MAP_ID` is an acceptable fallback only during local development.
- `.env.local` remains excluded from Git.
- No real API key belongs in source, output, tests, or documentation.

## 5. Rows included in the map

The runtime dataset is the explicit, user-curated Paris MVP selection produced by the export workflow:

- exactly 300 restaurants;
- exactly 15 restaurants in each arrondissement from 1 through 20;
- valid, finite coordinates;
- unique Google Place IDs used as public item IDs;
- only rows approved by the upstream MVP selection and eligibility rules.

The app validates those invariants and fails clearly if the canonical export no longer satisfies them. It does not query or re-evaluate Google Sheet rows at runtime.

## 6. RestaurantMapItem data contract

The server passes exactly this approved public contract to the client:

```ts
type RestaurantAvailability = "TRUE" | "FALSE" | "UNKNOWN";

type RestaurantMapItem = {
  id: string;
  name: string;
  googlePlaceId: string;
  address: string | null;
  city: string | null;
  postalCode: string | null;
  arrondissement: number;
  town: string | null;
  latitude: number;
  longitude: number;
  website: string | null;
  instagram: string | null;
  cuisine: string[];
  vibe: string[];
  features: string[];
  delivery: RestaurantAvailability;
  takeaway: RestaurantAvailability;
  favorite: string | null;
  notes: string | null;
};
```

The validator rejects a non-array top level, missing or extra fields, invalid identity/name/coordinate/arrondissement values, duplicate IDs, invalid optional fields, duplicate or invalid tags, invalid availability values, incorrect total count, missing arrondissements, or incorrect per-arrondissement counts.

Examples of internal fields that must not reach the browser:

- Canonical Key;
- Status;
- Needs Review;
- Review Reason;
- Match Method;
- Confidence;
- Source;
- LLM Confidence;
- LLM Evidence;
- LLM Model;
- LLM Tagged at;
- LLM Review Needed;
- Include in MVP;
- MVP Selection Reason;
- Geocode Cache;
- Map Location;
- sheet row numbers or export/debug metadata.

## 7. Implemented map and selection behavior

Session 14 implemented:

- initial viewport bounds fitted to all 300 Paris coordinates;
- one `AdvancedMarkerElement` per restaurant;
- descriptive marker titles using restaurant name and arrondissement;
- clustering of the same 300 prepared marker objects;
- restrained controls with zoom and keyboard interaction preserved;
- marker-click selection;
- visibly distinct selected-marker styling;
- panning the selected restaurant into view without automatic zoom changes;
- updating one detail UI when another marker is selected;
- explicit close/deselect behavior;
- cleanup of marker listeners, markers, clusterer state, and map listeners on final unmount;
- clear missing-key, missing-production-map-ID, loading, and API-load-failure states.

Implemented restaurant details:

- name;
- address;
- arrondissement;
- Cuisine only when populated;
- Vibe only when populated;
- website only when present and safe;
- Instagram only when present and safe.

External links open in a new tab with `rel="noopener noreferrer"`.

## 8. Desktop UX

Implemented desktop behavior:

- full usable map canvas;
- compact overlaid collection/status panel;
- overlaid restaurant detail panel when a marker is selected;
- map controls and detail content remain readable without horizontal overflow;
- keyboard interaction, semantic controls, accessible labels, and focus-visible styling are preserved.

A searchable restaurant list and list-to-map synchronization are not implemented yet.

## 9. Mobile UX

Implemented mobile behavior:

- full usable map;
- compact bottom-sheet-style restaurant detail card;
- close/deselect control;
- usable map controls around the detail sheet;
- no horizontal overflow at the verified 390 × 844 viewport.

A mobile discovery/search list is not implemented yet.

## 10. Next unimplemented MVP layer: discovery controls

The next MVP layer is limited to:

- searchable restaurant list;
- text search over dependable populated fields;
- arrondissement filtering;
- synchronizing list selection with map selection;
- updating visible markers, clusters, and result counts;
- responsive desktop/mobile discovery UI;
- empty-result and clear-filter behavior.

Cuisine and Vibe coverage is incomplete. Whether future filters should expose and explain that incompleteness remains an open product question. Features coverage is too sparse to be part of the next milestone.

## 11. Explicitly deferred

Do not include these in Session 15:

- Features filtering;
- enrichment;
- editing or adding restaurants from the app;
- directions or route planning;
- Near Me or browser geolocation;
- authentication redesign;
- deployment;
- advanced multi-filter logic;
- public sharing;
- user accounts or role-based permissions;
- delivery/takeaway enrichment;
- Google Places reprocessing or lookups.

## 12. Recommended Session 15

Recommended next session:

```text
Session 15 — Restaurant discovery controls
```

Reason:

The map works with all 300 restaurants, but users need a faster way to locate and browse restaurants without navigating clusters manually.
