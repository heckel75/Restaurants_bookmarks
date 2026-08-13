/** @typedef {import("../types/restaurant").RestaurantMapItem} RestaurantMapItem */

const EXPECTED_RESTAURANT_COUNT = 300;
const EXPECTED_ARRONDISSEMENT_COUNT = 20;
const EXPECTED_RESTAURANTS_PER_ARRONDISSEMENT = 15;
const AVAILABILITY_VALUES = new Set(["TRUE", "FALSE", "UNKNOWN"]);
const PUBLIC_FIELDS = [
  "id",
  "name",
  "googlePlaceId",
  "address",
  "city",
  "postalCode",
  "arrondissement",
  "town",
  "latitude",
  "longitude",
  "website",
  "instagram",
  "cuisine",
  "vibe",
  "features",
  "delivery",
  "takeaway",
  "favorite",
  "notes",
];
const PUBLIC_FIELD_SET = new Set(PUBLIC_FIELDS);
const NULLABLE_STRING_FIELDS = [
  "address",
  "city",
  "postalCode",
  "town",
  "website",
  "instagram",
  "favorite",
  "notes",
];
const TAG_FIELDS = ["cuisine", "vibe", "features"];

export class RestaurantDataValidationError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = "RestaurantDataValidationError";
  }
}

/** @param {unknown} value */
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Validate the canonical JSON at runtime before the app uses it.
 * @param {unknown} value
 * @returns {RestaurantMapItem[]}
 */
export function validateRestaurantMapItems(value) {
  if (!Array.isArray(value)) {
    throw new RestaurantDataValidationError(
      "restaurants.json must contain a top-level JSON array.",
    );
  }

  if (value.length !== EXPECTED_RESTAURANT_COUNT) {
    throw new RestaurantDataValidationError(
      `restaurants.json must contain exactly ${EXPECTED_RESTAURANT_COUNT} restaurants; found ${value.length}.`,
    );
  }

  const ids = new Set();
  const arrondissementCounts = new Map();

  for (const [index, item] of value.entries()) {
    const location = `restaurants[${index}]`;
    if (!isRecord(item)) {
      throw new RestaurantDataValidationError(`${location} must be an object.`);
    }

    const keys = Object.keys(item);
    const missingFields = PUBLIC_FIELDS.filter((field) => !(field in item));
    const extraFields = keys.filter((field) => !PUBLIC_FIELD_SET.has(field));
    if (missingFields.length > 0 || extraFields.length > 0) {
      throw new RestaurantDataValidationError(
        `${location} does not match the public contract (missing: ${missingFields.join(", ") || "none"}; extra: ${extraFields.join(", ") || "none"}).`,
      );
    }

    for (const field of ["id", "name", "googlePlaceId"]) {
      const fieldValue = item[field];
      if (
        typeof fieldValue !== "string" ||
        fieldValue.length === 0 ||
        fieldValue !== fieldValue.trim()
      ) {
        throw new RestaurantDataValidationError(
          `${location}.${field} must be a trimmed, non-empty string.`,
        );
      }
    }

    if (item.id !== item.googlePlaceId) {
      throw new RestaurantDataValidationError(
        `${location}.id must equal ${location}.googlePlaceId.`,
      );
    }
    if (ids.has(item.id)) {
      throw new RestaurantDataValidationError(
        `Duplicate restaurant ID ${JSON.stringify(item.id)} found at ${location}.`,
      );
    }
    ids.add(item.id);

    for (const [field, minimum, maximum] of [
      ["latitude", -90, 90],
      ["longitude", -180, 180],
    ]) {
      const coordinate = item[field];
      if (
        typeof coordinate !== "number" ||
        !Number.isFinite(coordinate) ||
        coordinate < minimum ||
        coordinate > maximum
      ) {
        throw new RestaurantDataValidationError(
          `${location}.${field} must be a finite number from ${minimum} through ${maximum}.`,
        );
      }
    }

    if (
      !Number.isInteger(item.arrondissement) ||
      item.arrondissement < 1 ||
      item.arrondissement > EXPECTED_ARRONDISSEMENT_COUNT
    ) {
      throw new RestaurantDataValidationError(
        `${location}.arrondissement must be an integer from 1 through ${EXPECTED_ARRONDISSEMENT_COUNT}.`,
      );
    }
    arrondissementCounts.set(
      item.arrondissement,
      (arrondissementCounts.get(item.arrondissement) ?? 0) + 1,
    );

    for (const field of NULLABLE_STRING_FIELDS) {
      const fieldValue = item[field];
      if (
        fieldValue !== null &&
        (typeof fieldValue !== "string" ||
          fieldValue.length === 0 ||
          fieldValue !== fieldValue.trim())
      ) {
        throw new RestaurantDataValidationError(
          `${location}.${field} must be null or a trimmed, non-empty string.`,
        );
      }
    }

    for (const field of TAG_FIELDS) {
      const tags = item[field];
      if (!Array.isArray(tags)) {
        throw new RestaurantDataValidationError(`${location}.${field} must be an array.`);
      }
      const normalizedTags = new Set();
      for (const tag of tags) {
        if (typeof tag !== "string" || tag.length === 0 || tag !== tag.trim()) {
          throw new RestaurantDataValidationError(
            `${location}.${field} must contain only trimmed, non-empty strings.`,
          );
        }
        const normalizedTag = tag.toLocaleLowerCase("en");
        if (normalizedTags.has(normalizedTag)) {
          throw new RestaurantDataValidationError(
            `${location}.${field} contains duplicate tag ${JSON.stringify(tag)}.`,
          );
        }
        normalizedTags.add(normalizedTag);
      }
    }

    for (const field of ["delivery", "takeaway"]) {
      if (!AVAILABILITY_VALUES.has(item[field])) {
        throw new RestaurantDataValidationError(
          `${location}.${field} must be TRUE, FALSE, or UNKNOWN.`,
        );
      }
    }
  }

  if (arrondissementCounts.size !== EXPECTED_ARRONDISSEMENT_COUNT) {
    throw new RestaurantDataValidationError(
      `Expected all ${EXPECTED_ARRONDISSEMENT_COUNT} arrondissements; found ${arrondissementCounts.size}.`,
    );
  }
  for (let arrondissement = 1; arrondissement <= EXPECTED_ARRONDISSEMENT_COUNT; arrondissement += 1) {
    const count = arrondissementCounts.get(arrondissement) ?? 0;
    if (count !== EXPECTED_RESTAURANTS_PER_ARRONDISSEMENT) {
      throw new RestaurantDataValidationError(
        `Arrondissement ${arrondissement} must contain exactly ${EXPECTED_RESTAURANTS_PER_ARRONDISSEMENT} restaurants; found ${count}.`,
      );
    }
  }

  return value;
}
