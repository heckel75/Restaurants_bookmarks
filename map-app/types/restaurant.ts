export type RestaurantAvailability = "TRUE" | "FALSE" | "UNKNOWN";

/** The exact browser-safe contract generated in data/map_mvp/restaurants.json. */
export type RestaurantMapItem = {
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
