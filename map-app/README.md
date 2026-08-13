# Paris Restaurant Map

Private Next.js map for the validated 300-restaurant Paris dataset. The server reads and validates the canonical `../data/map_mvp/restaurants.json`; the client receives only its approved 19-field `RestaurantMapItem[]` objects.

## Setup

Requirements: Node.js 20.9 or newer and npm.

```bash
npm install
```

Copy `.env.local.example` to `.env.local` and add a browser-restricted Google Maps JavaScript API key:

```dotenv
NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=
NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID=
```

A map ID is required for Advanced Markers. During `npm run dev` only, a blank map ID automatically uses Google's `DEMO_MAP_ID`; set a real map ID for deployed builds. Do not commit `.env.local`. Restrict the browser key to the intended local/deployed origins and the Maps JavaScript API.

```bash
npm run dev
```

Open <http://localhost:3000>. The app makes no Google Sheets, Places, geocoding, or other restaurant-data requests at runtime.

## Checks

```bash
npm run validate:data
npm test
npm run lint
npm run build
```
