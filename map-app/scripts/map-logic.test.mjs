import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  createClusteredMarkerSet,
  findRestaurantById,
  getRestaurantDetailModel,
  prepareMapInput,
} from "../lib/map-logic.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const datasetPath = resolve(
  scriptDirectory,
  "..",
  "..",
  "data",
  "map_mvp",
  "restaurants.json",
);
const canonicalRestaurants = JSON.parse(await readFile(datasetPath, "utf8"));

test("prepares all 300 marker inputs and bounds for all 20 arrondissements", () => {
  const mapInput = prepareMapInput(canonicalRestaurants);
  assert.equal(mapInput.markers.length, 300);
  assert.equal(new Set(mapInput.markers.map((marker) => marker.arrondissement)).size, 20);
  assert.ok(mapInput.bounds.north > mapInput.bounds.south);
  assert.ok(mapInput.bounds.east > mapInput.bounds.west);
  assert.ok(
    mapInput.markers.every(
      (marker) =>
        marker.position.lat >= mapInput.bounds.south &&
        marker.position.lat <= mapInput.bounds.north &&
        marker.position.lng >= mapInput.bounds.west &&
        marker.position.lng <= mapInput.bounds.east,
    ),
  );
});

test("creates exactly 300 marker objects and supplies the same array to clustering", () => {
  const mapInput = prepareMapInput(canonicalRestaurants);
  let clusteredMarkers;
  const result = createClusteredMarkerSet(
    mapInput.markers,
    (input) => ({ restaurantId: input.id }),
    (markers) => {
      clusteredMarkers = markers;
      return { markerCount: markers.length };
    },
  );

  assert.equal(result.markers.length, 300);
  assert.strictEqual(clusteredMarkers, result.markers);
  assert.equal(result.clusterer.markerCount, 300);
  assert.equal(new Set(result.markers).size, 300);
});

test("finds a selected restaurant and safely handles empty or unknown IDs", () => {
  const expected = canonicalRestaurants[42];
  assert.strictEqual(findRestaurantById(canonicalRestaurants, expected.id), expected);
  assert.equal(findRestaurantById(canonicalRestaurants, null), null);
  assert.equal(findRestaurantById(canonicalRestaurants, "missing-id"), null);
});

test("shows optional details only when populated and links are safe", () => {
  const restaurant = {
    ...canonicalRestaurants[0],
    cuisine: [],
    vibe: [],
    website: null,
    instagram: "javascript:alert(1)",
  };
  const emptyDetails = getRestaurantDetailModel(restaurant);
  assert.equal(emptyDetails.cuisine, null);
  assert.equal(emptyDetails.vibe, null);
  assert.equal(emptyDetails.websiteUrl, null);
  assert.equal(emptyDetails.instagramUrl, null);

  const populatedDetails = getRestaurantDetailModel({
    ...restaurant,
    cuisine: ["French"],
    vibe: ["Classic"],
    website: "https://example.com/menu",
    instagram: "https://www.instagram.com/example/",
  });
  assert.deepEqual(populatedDetails.cuisine, ["French"]);
  assert.deepEqual(populatedDetails.vibe, ["Classic"]);
  assert.equal(populatedDetails.websiteUrl, "https://example.com/menu");
  assert.equal(populatedDetails.instagramUrl, "https://www.instagram.com/example/");
});

test("rejects invalid coordinates before map initialization", () => {
  const restaurants = structuredClone(canonicalRestaurants);
  restaurants[0].longitude = Infinity;
  assert.throws(() => prepareMapInput(restaurants), /has invalid coordinates/);
});
