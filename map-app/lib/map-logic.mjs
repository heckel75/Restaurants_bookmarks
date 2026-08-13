/** @typedef {import("../types/restaurant").RestaurantMapItem} RestaurantMapItem */

/**
 * @typedef {Object} PreparedMarkerInput
 * @property {string} id
 * @property {string} title
 * @property {number} arrondissement
 * @property {{lat: number, lng: number}} position
 */

/**
 * Prepare deterministic marker inputs and the initial viewport bounds.
 * @param {RestaurantMapItem[]} restaurants
 */
export function prepareMapInput(restaurants) {
  if (!Array.isArray(restaurants) || restaurants.length === 0) {
    throw new Error("At least one restaurant is required to prepare the map.");
  }

  /** @type {PreparedMarkerInput[]} */
  const markers = [];
  let north = -Infinity;
  let south = Infinity;
  let east = -Infinity;
  let west = Infinity;

  for (const restaurant of restaurants) {
    const { latitude, longitude } = restaurant;
    if (
      typeof latitude !== "number" ||
      !Number.isFinite(latitude) ||
      latitude < -90 ||
      latitude > 90 ||
      typeof longitude !== "number" ||
      !Number.isFinite(longitude) ||
      longitude < -180 ||
      longitude > 180
    ) {
      throw new Error(`Restaurant ${JSON.stringify(restaurant.id)} has invalid coordinates.`);
    }

    markers.push({
      id: restaurant.id,
      title: restaurant.name,
      arrondissement: restaurant.arrondissement,
      position: { lat: latitude, lng: longitude },
    });
    north = Math.max(north, latitude);
    south = Math.min(south, latitude);
    east = Math.max(east, longitude);
    west = Math.min(west, longitude);
  }

  return {
    markers,
    bounds: { north, south, east, west },
  };
}

/**
 * @param {RestaurantMapItem[]} restaurants
 * @param {string | null} selectedId
 * @returns {RestaurantMapItem | null}
 */
export function findRestaurantById(restaurants, selectedId) {
  if (!selectedId) {
    return null;
  }
  return restaurants.find((restaurant) => restaurant.id === selectedId) ?? null;
}

/** @param {string | null} value */
function safeExternalUrl(value) {
  if (!value) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

/** @param {number} arrondissement */
function formatArrondissement(arrondissement) {
  return `${arrondissement}${arrondissement === 1 ? "er" : "e"} arrondissement`;
}

/** @param {RestaurantMapItem} restaurant */
export function getRestaurantDetailModel(restaurant) {
  return {
    address: restaurant.address ?? "Address unavailable",
    arrondissement: formatArrondissement(restaurant.arrondissement),
    cuisine: restaurant.cuisine.length > 0 ? restaurant.cuisine : null,
    vibe: restaurant.vibe.length > 0 ? restaurant.vibe : null,
    websiteUrl: safeExternalUrl(restaurant.website),
    instagramUrl: safeExternalUrl(restaurant.instagram),
  };
}

/**
 * Create every marker first, then pass that exact array to the clusterer factory.
 * @template TMarker
 * @template TClusterer
 * @param {PreparedMarkerInput[]} markerInputs
 * @param {(input: PreparedMarkerInput) => TMarker} createMarker
 * @param {(markers: TMarker[]) => TClusterer} createClusterer
 */
export function createClusteredMarkerSet(markerInputs, createMarker, createClusterer) {
  const markers = markerInputs.map(createMarker);
  if (markers.length !== markerInputs.length) {
    throw new Error("Marker creation did not preserve the prepared input count.");
  }
  const clusterer = createClusterer(markers);
  return { markers, clusterer };
}
