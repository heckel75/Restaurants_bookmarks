import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { validateRestaurantMapItems } from "../lib/validate-restaurant-data.mjs";

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

function freshRestaurants() {
  return structuredClone(canonicalRestaurants);
}

test("accepts the canonical 300-restaurant dataset", () => {
  assert.equal(validateRestaurantMapItems(freshRestaurants()).length, 300);
});

test("rejects a non-array top-level value", () => {
  assert.throws(
    () => validateRestaurantMapItems({}),
    /top-level JSON array/,
  );
});

test("rejects invalid identity fields", () => {
  const restaurants = freshRestaurants();
  restaurants[0].googlePlaceId = "different-id";
  assert.throws(
    () => validateRestaurantMapItems(restaurants),
    /id must equal .*googlePlaceId/,
  );
});

test("rejects invalid restaurant names", () => {
  const restaurants = freshRestaurants();
  restaurants[0].name = " ";
  assert.throws(
    () => validateRestaurantMapItems(restaurants),
    /name must be a trimmed, non-empty string/,
  );
});

test("rejects invalid coordinates", () => {
  const restaurants = freshRestaurants();
  restaurants[0].latitude = 91;
  assert.throws(
    () => validateRestaurantMapItems(restaurants),
    /latitude must be a finite number from -90 through 90/,
  );
});

test("rejects invalid arrondissements", () => {
  const restaurants = freshRestaurants();
  restaurants[0].arrondissement = "1";
  assert.throws(
    () => validateRestaurantMapItems(restaurants),
    /arrondissement must be an integer from 1 through 20/,
  );
});

test("rejects duplicate IDs", () => {
  const restaurants = freshRestaurants();
  restaurants[1].id = restaurants[0].id;
  restaurants[1].googlePlaceId = restaurants[0].googlePlaceId;
  assert.throws(
    () => validateRestaurantMapItems(restaurants),
    /Duplicate restaurant ID/,
  );
});
