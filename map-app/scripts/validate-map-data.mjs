import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
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

try {
  const rawJson = await readFile(datasetPath, "utf8");
  const restaurants = validateRestaurantMapItems(JSON.parse(rawJson));
  const counts = new Map();
  for (const restaurant of restaurants) {
    counts.set(
      restaurant.arrondissement,
      (counts.get(restaurant.arrondissement) ?? 0) + 1,
    );
  }

  console.log(`Dataset validation: PASS`);
  console.log(`Canonical file: ${datasetPath}`);
  console.log(`Restaurants: ${restaurants.length}`);
  console.log(`Arrondissements: ${counts.size}`);
  console.log(
    `Per-arrondissement counts: ${[...counts.entries()]
      .sort(([left], [right]) => left - right)
      .map(([arrondissement, count]) => `${arrondissement}=${count}`)
      .join(", ")}`,
  );
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Dataset validation: FAIL\n${message}`);
  process.exitCode = 1;
}
