# Restaurant Map App MVP Spec

## 1. Purpose

A private, mobile-friendly restaurant map app for the Paris/suburbs restaurant database.

The app will use Google Sheets as the source of truth and show validated restaurants on top of Google Maps.

## 2. Deployment decision

MVP deployment:

- Private online app
- Password protected
- Deployed on Vercel or equivalent private web hosting
- Not local-only
- No public sharing in MVP

## 3. App stack

MVP stack:

- Next.js
- Google Maps JavaScript API
- Google Sheets API as read source
- Server-side API route for reading sheet data
- Environment variables for secrets
- Simple password protection for MVP

## 4. Data source and privacy

Google Sheet:

- Keep the production Google Sheet private.
- Do not publish the sheet to the web.
- Share the sheet read-only with a Google service-account email.

Next.js app:

- Server-side API route reads the Google Sheet.
- Client receives only safe restaurant display/filter fields.
- Password protection gates both the app page and the restaurant-data API.

Secrets:

- Store Google service-account credentials in environment variables.
- Store app password in environment variables.
- Store Google Maps browser API key in environment variables.
- Restrict the Maps API key to the deployed app domain and required APIs.

## 5. Rows included in the map

Only include rows where:

- Status = active
- Needs Review = FALSE
- Latitude is filled
- Longitude is filled

Rows that are closed, archived, not relevant, missing coordinates, or still needing review should not appear in the MVP map.

## 6. RestaurantMapItem data contract

The server should send only this safe MVP object to the browser:

```ts
type RestaurantMapItem = {
  id: string;
  name: string;
  googlePlaceId?: string;
  address?: string;
  city?: string;
  postalCode?: string;
  arrondissement?: string;
  town?: string;
  latitude: number;
  longitude: number;
  website?: string;
  instagram?: string;
  cuisine: string[];
  vibe: string[];
  features: string[];
  delivery: "TRUE" | "FALSE" | "UNKNOWN";
  takeaway: "TRUE" | "FALSE" | "UNKNOWN";
  favorite?: string;
  notes?: string;
  lastChecked?: string;
};
```

Do not send these internal fields to the browser in MVP:

- Canonical Key
- Review Reason
- Match Method
- Confidence
- Source
- LLM Evidence
- LLM Model
- LLM Tagged at
- Geocode Cache
- Map Location

## 7. MVP map behavior

Map:

- Google Maps centered on Paris by default.
- Show one marker per included restaurant.
- Clicking a marker opens restaurant detail.

Restaurant card:

- Name
- Address
- Cuisine
- Vibe
- Features
- Delivery
- Takeaway
- Favorite
- Notes
- Website link
- Instagram link

Sidebar:

- Search by restaurant name.
- Click restaurant in list to focus marker.
- Click marker to open restaurant detail.

Filters:

- Favorite
- Arrondissement
- Town
- Cuisine
- Vibe
- Features
- Delivery
- Takeaway

## 8. Desktop UX

- Map on the right.
- Sidebar/list on the left.
- Search and filters at the top of the sidebar.
- Clicking a restaurant focuses the marker and opens the detail card.
- Default list sorting:
  - Favorites first
  - Then alphabetical by name

## 9. Mobile UX

- Full-screen map first.
- Floating search/filter button.
- Bottom sheet for restaurant list and restaurant detail.
- Tap marker to open bottom sheet.
- Tap restaurant in list to focus marker.

Useful mobile controls:

- Reset to Paris
- Clear filters
- Optional "near me" button using browser location permission

## 10. Explicitly not in MVP

Do not include these in the first MVP:

- Editing restaurants from the app
- Adding restaurants from the app
- Route planning
- Public sharing
- User accounts
- Complex role-based permissions
- Full authentication system
- Delivery/takeaway enrichment
- Google Places reprocessing

## 11. Recommended Session 13

Recommended next session:

Session 13 - Private map app scaffold

Goal:

Create the initial app skeleton without connecting production secrets yet.

Suggested Session 13 scope:

- Decide whether the app lives inside this repository or in a separate repository/folder.
- Create a Next.js scaffold.
- Add environment-variable template.
- Add placeholder password gate.
- Add mocked RestaurantMapItem data.
- Render first static map/list UI without touching the live Google Sheet yet.
