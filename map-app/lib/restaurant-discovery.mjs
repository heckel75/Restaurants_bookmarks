/** @typedef {import("../types/restaurant").RestaurantMapItem} RestaurantMapItem */

/**
 * @typedef {Object} RestaurantDiscoveryCriteria
 * @property {string} search
 * @property {number | null} arrondissement
 */

/** @type {Readonly<RestaurantDiscoveryCriteria>} */
export const EMPTY_DISCOVERY_CRITERIA = Object.freeze({
  search: "",
  arrondissement: null,
});

/** @param {unknown} value */
export function normalizeSearchText(value) {
  if (typeof value !== "string") {
    return "";
  }

  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("fr")
    .replaceAll("œ", "oe")
    .replaceAll("æ", "ae")
    .trim();
}

/**
 * Filter without mutating the canonical restaurant array or changing its order.
 * @param {RestaurantMapItem[]} restaurants
 * @param {RestaurantDiscoveryCriteria} criteria
 */
export function filterRestaurants(restaurants, criteria) {
  const normalizedSearch = normalizeSearchText(criteria.search);

  return restaurants.filter((restaurant) => {
    if (
      criteria.arrondissement !== null &&
      restaurant.arrondissement !== criteria.arrondissement
    ) {
      return false;
    }

    if (!normalizedSearch) {
      return true;
    }

    return [restaurant.name, restaurant.address, restaurant.postalCode].some((value) =>
      normalizeSearchText(value).includes(normalizedSearch),
    );
  });
}

/** @param {RestaurantMapItem[]} restaurants */
export function getVisibleRestaurantIds(restaurants) {
  return new Set(restaurants.map((restaurant) => restaurant.id));
}

/**
 * @param {string | null} selectedId
 * @param {Set<string>} visibleRestaurantIds
 */
export function selectedRestaurantRemainsVisible(selectedId, visibleRestaurantIds) {
  return selectedId === null || visibleRestaurantIds.has(selectedId);
}
