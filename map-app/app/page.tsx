import RestaurantMap from "@/components/restaurant-map";
import { getRestaurantMapItems } from "@/lib/restaurants";
import styles from "./page.module.css";

export default function Home() {
  const restaurants = getRestaurantMapItems();
  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY?.trim() ?? "";
  const configuredMapId = process.env.NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID?.trim() ?? "";
  const usingDemoMapId = !configuredMapId && process.env.NODE_ENV === "development";
  const mapId = configuredMapId || (usingDemoMapId ? "DEMO_MAP_ID" : "");

  return (
    <main className={styles.shell}>
      <RestaurantMap
        restaurants={restaurants}
        apiKey={apiKey}
        mapId={mapId}
        usingDemoMapId={usingDemoMapId}
      />
    </main>
  );
}
