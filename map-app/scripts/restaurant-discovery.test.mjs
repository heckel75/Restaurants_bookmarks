import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  EMPTY_DISCOVERY_CRITERIA,
  filterRestaurants,
  getVisibleRestaurantIds,
  selectedRestaurantRemainsVisible,
} from "../lib/restaurant-discovery.mjs";

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

test("no discovery criteria returns all 300 restaurants in dataset order", () => {
  const results = filterRestaurants(canonicalRestaurants, EMPTY_DISCOVERY_CRITERIA);
  assert.equal(results.length, 300);
  assert.notStrictEqual(results, canonicalRestaurants);
  assert.deepEqual(
    results.map((restaurant) => restaurant.id),
    canonicalRestaurants.map((restaurant) => restaurant.id),
  );
});

test("search is case-insensitive", () => {
  const expected = canonicalRestaurants.find(
    (restaurant) => restaurant.name === "Café Joséphine",
  );
  assert.ok(expected);

  const results = filterRestaurants(canonicalRestaurants, {
    search: "CAFÉ JOSÉPHINE",
    arrondissement: null,
  });
  assert.ok(results.some((restaurant) => restaurant.id === expected.id));
});

test("search is accent-insensitive", () => {
  const expected = canonicalRestaurants.find(
    (restaurant) => restaurant.name === "Café Joséphine",
  );
  assert.ok(expected);

  const results = filterRestaurants(canonicalRestaurants, {
    search: "cafe josephine",
    arrondissement: null,
  });
  assert.ok(results.some((restaurant) => restaurant.id === expected.id));
});

test("search matches restaurant addresses", () => {
  const results = filterRestaurants(canonicalRestaurants, {
    search: "pont aux choux",
    arrondissement: null,
  });
  assert.ok(results.length > 0);
  assert.ok(
    results.every((restaurant) =>
      restaurant.address.toLocaleLowerCase("fr").includes("pont aux choux"),
    ),
  );
});

test("search matches postal codes", () => {
  const results = filterRestaurants(canonicalRestaurants, {
    search: "75012",
    arrondissement: null,
  });
  assert.equal(results.length, 15);
  assert.ok(results.every((restaurant) => restaurant.postalCode === "75012"));
});

test("arrondissement filtering returns the curated 15 restaurants", () => {
  const results = filterRestaurants(canonicalRestaurants, {
    search: "",
    arrondissement: 7,
  });
  assert.equal(results.length, 15);
  assert.ok(results.every((restaurant) => restaurant.arrondissement === 7));
});

test("search and arrondissement use AND semantics", () => {
  const results = filterRestaurants(canonicalRestaurants, {
    search: "cafe",
    arrondissement: 7,
  });
  assert.ok(results.length > 0);
  assert.ok(results.length < 15);
  assert.ok(results.every((restaurant) => restaurant.arrondissement === 7));
  assert.ok(
    results.every((restaurant) =>
      [restaurant.name, restaurant.address, restaurant.postalCode].some((value) =>
        value.toLocaleLowerCase("fr").includes("cafe") ||
        value.toLocaleLowerCase("fr").includes("café"),
      ),
    ),
  );
});

test("unmatched criteria return an empty result collection", () => {
  const results = filterRestaurants(canonicalRestaurants, {
    search: "restaurant that does not exist in paris",
    arrondissement: 20,
  });
  assert.deepEqual(results, []);
});

test("clearing active criteria restores all 300 restaurants", () => {
  const filtered = filterRestaurants(canonicalRestaurants, {
    search: "cafe",
    arrondissement: 7,
  });
  assert.ok(filtered.length < canonicalRestaurants.length);

  const restored = filterRestaurants(canonicalRestaurants, EMPTY_DISCOVERY_CRITERIA);
  assert.equal(restored.length, 300);
});

test("filtered restaurant IDs exactly determine visible marker IDs", () => {
  const filtered = filterRestaurants(canonicalRestaurants, {
    search: "cafe",
    arrondissement: 7,
  });
  const visibleRestaurantIds = getVisibleRestaurantIds(filtered);
  const visibleMarkerIds = canonicalRestaurants
    .filter((restaurant) => visibleRestaurantIds.has(restaurant.id))
    .map((restaurant) => restaurant.id);

  assert.deepEqual(
    visibleMarkerIds,
    filtered.map((restaurant) => restaurant.id),
  );
});

test("an existing selection remains selected while it is visible", () => {
  const filtered = filterRestaurants(canonicalRestaurants, {
    search: "75007",
    arrondissement: 7,
  });
  const visibleRestaurantIds = getVisibleRestaurantIds(filtered);
  assert.equal(
    selectedRestaurantRemainsVisible(filtered[0].id, visibleRestaurantIds),
    true,
  );
});

test("an existing selection clears when discovery criteria filter it out", () => {
  const selectedId = canonicalRestaurants[0].id;
  const filtered = filterRestaurants(canonicalRestaurants, {
    search: "",
    arrondissement: 20,
  });
  const visibleRestaurantIds = getVisibleRestaurantIds(filtered);
  assert.equal(selectedRestaurantRemainsVisible(selectedId, visibleRestaurantIds), false);
});
