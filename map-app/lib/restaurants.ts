import rawRestaurants from "../../data/map_mvp/restaurants.json";
import { validateRestaurantMapItems } from "./validate-restaurant-data.mjs";

const restaurants = validateRestaurantMapItems(rawRestaurants);

export function getRestaurantMapItems() {
  return restaurants;
}

export function getRestaurantDatasetSummary() {
  const arrondissementCounts = new Map<number, number>();
  for (const restaurant of restaurants) {
    arrondissementCounts.set(
      restaurant.arrondissement,
      (arrondissementCounts.get(restaurant.arrondissement) ?? 0) + 1,
    );
  }

  const perArrondissement = new Set(arrondissementCounts.values());

  return {
    status: "Loaded and validated from the canonical JSON export",
    restaurantCount: restaurants.length,
    arrondissementCount: arrondissementCounts.size,
    restaurantsPerArrondissement:
      perArrondissement.size === 1 ? [...perArrondissement][0] : "Mixed",
  } as const;
}
