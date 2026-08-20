"use client";

import { MarkerClusterer, type Renderer } from "@googlemaps/markerclusterer";
import {
  type ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  createClusteredMarkerSet,
  findRestaurantById,
  getRestaurantDetailModel,
  prepareMapInput,
} from "@/lib/map-logic.mjs";
import {
  EMPTY_DISCOVERY_CRITERIA,
  filterRestaurants,
  getVisibleRestaurantIds,
  selectedRestaurantRemainsVisible,
} from "@/lib/restaurant-discovery.mjs";
import type { RestaurantMapItem } from "@/types/restaurant";
import { loadGoogleMapsLibraries } from "./google-maps-loader";
import styles from "./restaurant-map.module.css";

type RestaurantMapProps = {
  restaurants: RestaurantMapItem[];
  apiKey: string;
  mapId: string;
  usingDemoMapId: boolean;
};

type MarkerRecord = {
  restaurantId: string;
  title: string;
  position: google.maps.LatLngLiteral;
  marker: google.maps.marker.AdvancedMarkerElement;
  visual: HTMLSpanElement;
  clickHandler: EventListener;
};

type MapRuntime = {
  container: HTMLDivElement;
  map: google.maps.Map;
  clusterer: MarkerClusterer;
  markerRecords: Map<string, MarkerRecord>;
  disposed: boolean;
};

type LoadStatus = "loading" | "ready" | "error";

const ARRONDISSEMENTS = Array.from({ length: 20 }, (_, index) => index + 1);

function formatListArrondissement(arrondissement: number) {
  return `Paris ${arrondissement}${arrondissement === 1 ? "er" : "e"}`;
}

function createClusterRenderer(
  AdvancedMarkerElement: typeof google.maps.marker.AdvancedMarkerElement,
): Renderer {
  return {
    render({ count, position }) {
      const visual = document.createElement("span");
      visual.className = styles.clusterMarker;
      visual.textContent = String(count);
      visual.setAttribute("aria-hidden", "true");

      const marker = new AdvancedMarkerElement({
        position,
        title: `Cluster containing ${count} restaurants`,
        gmpClickable: true,
        zIndex: 10_000 + count,
      });
      marker.append(visual);
      return marker;
    },
  };
}

async function initializeMapRuntime({
  container,
  apiKey,
  mapId,
  mapInput,
  onSelect,
}: {
  container: HTMLDivElement;
  apiKey: string;
  mapId: string;
  mapInput: ReturnType<typeof prepareMapInput>;
  onSelect: (restaurantId: string) => void;
}): Promise<MapRuntime> {
  const { core, maps, marker } = await loadGoogleMapsLibraries(apiKey, mapId);
  const map = new maps.Map(container, {
    center: { lat: 48.8566, lng: 2.3522 },
    zoom: 12,
    mapId,
    backgroundColor: "#e6e1d4",
    clickableIcons: false,
    disableDefaultUI: true,
    gestureHandling: "greedy",
    keyboardShortcuts: true,
    zoomControl: true,
    zoomControlOptions: {
      position: core.ControlPosition.LEFT_CENTER,
    },
  });

  const markerRecords = new Map<string, MarkerRecord>();
  const clusterRenderer = createClusterRenderer(marker.AdvancedMarkerElement);
  const { clusterer } = createClusteredMarkerSet(
    mapInput.markers,
    (input) => {
      const visual = document.createElement("span");
      visual.className = styles.restaurantMarker;
      visual.dataset.selected = "false";
      visual.setAttribute("aria-hidden", "true");

      const advancedMarker = new marker.AdvancedMarkerElement({
        position: input.position,
        title: `${input.title}, ${input.arrondissement}${input.arrondissement === 1 ? "er" : "e"} arrondissement`,
        gmpClickable: true,
      });
      advancedMarker.append(visual);

      const clickHandler: EventListener = () => onSelect(input.id);
      advancedMarker.addEventListener("gmp-click", clickHandler);
      markerRecords.set(input.id, {
        restaurantId: input.id,
        title: advancedMarker.title,
        position: input.position,
        marker: advancedMarker,
        visual,
        clickHandler,
      });
      return advancedMarker;
    },
    (markers) =>
      new MarkerClusterer({
        map,
        markers,
        renderer: clusterRenderer,
      }),
  );

  map.fitBounds(mapInput.bounds, 48);

  return {
    container,
    map,
    clusterer,
    markerRecords,
    disposed: false,
  };
}

function applySelection(runtime: MapRuntime, selectedId: string | null) {
  for (const record of runtime.markerRecords.values()) {
    const isSelected = record.restaurantId === selectedId;
    record.visual.dataset.selected = String(isSelected);
    record.marker.title = isSelected ? `${record.title} (selected)` : record.title;
    record.marker.zIndex = isSelected ? 1_000 : undefined;
  }

  if (selectedId) {
    const selectedRecord = runtime.markerRecords.get(selectedId);
    if (selectedRecord) {
      runtime.map.panTo(selectedRecord.position);
    }
  }
}

function applyVisibleMarkers(runtime: MapRuntime, visibleRestaurantIds: Set<string>) {
  const visibleMarkers: google.maps.marker.AdvancedMarkerElement[] = [];
  for (const record of runtime.markerRecords.values()) {
    if (visibleRestaurantIds.has(record.restaurantId)) {
      visibleMarkers.push(record.marker);
    }
  }

  runtime.clusterer.clearMarkers(true);
  runtime.clusterer.addMarkers(visibleMarkers);
}

function disposeMapRuntime(runtime: MapRuntime) {
  if (runtime.disposed) {
    return;
  }
  runtime.disposed = true;
  runtime.clusterer.clearMarkers(true);
  runtime.clusterer.setMap(null);

  for (const record of runtime.markerRecords.values()) {
    record.marker.removeEventListener("gmp-click", record.clickHandler);
    google.maps.event.clearInstanceListeners(record.marker);
    record.marker.map = null;
    record.marker.replaceChildren();
  }
  runtime.markerRecords.clear();
  google.maps.event.clearInstanceListeners(runtime.map);
  runtime.container.replaceChildren();
}

export default function RestaurantMap({
  restaurants,
  apiKey,
  mapId,
  usingDemoMapId,
}: RestaurantMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<MapRuntime | null>(null);
  const initializationRef = useRef<Promise<MapRuntime> | null>(null);
  const mountedRef = useRef(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const mobileBackButtonRef = useRef<HTMLButtonElement>(null);
  const resultButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [criteria, setCriteria] = useState({ ...EMPTY_DISCOVERY_CRITERIA });
  const mapInput = useMemo(() => prepareMapInput(restaurants), [restaurants]);
  const filteredRestaurants = useMemo(
    () => filterRestaurants(restaurants, criteria),
    [criteria, restaurants],
  );
  const visibleRestaurantIds = useMemo(
    () => getVisibleRestaurantIds(filteredRestaurants),
    [filteredRestaurants],
  );
  const selectedRestaurant = useMemo(
    () => findRestaurantById(restaurants, selectedId),
    [restaurants, selectedId],
  );
  const detail = useMemo(
    () => (selectedRestaurant ? getRestaurantDetailModel(selectedRestaurant) : null),
    [selectedRestaurant],
  );
  const criteriaActive =
    criteria.search.trim().length > 0 || criteria.arrondissement !== null;
  const configurationIssue = useMemo(
    () =>
      !apiKey
        ? {
            title: "Google Maps API key needed",
            message:
              "Add NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to map-app/.env.local, then restart the development server.",
          }
        : !mapId
          ? {
              title: "Google Maps map ID needed",
              message:
                "Add NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID to map-app/.env.local. DEMO_MAP_ID is used automatically only in local development.",
            }
          : null,
    [apiKey, mapId],
  );

  const handleSelect = useCallback((restaurantId: string) => {
    setSelectedId(restaurantId);
  }, []);

  const applyCriteria = useCallback(
    (nextCriteria: typeof criteria) => {
      setCriteria(nextCriteria);
      const nextVisibleRestaurantIds = getVisibleRestaurantIds(
        filterRestaurants(restaurants, nextCriteria),
      );
      if (!selectedRestaurantRemainsVisible(selectedId, nextVisibleRestaurantIds)) {
        setSelectedId(null);
      }
    },
    [restaurants, selectedId],
  );

  const handleSearchChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      applyCriteria({ ...criteria, search: event.target.value });
    },
    [applyCriteria, criteria],
  );

  const handleArrondissementChange = useCallback(
    (event: ChangeEvent<HTMLSelectElement>) => {
      const value = event.target.value;
      applyCriteria({
        ...criteria,
        arrondissement: value ? Number(value) : null,
      });
    },
    [applyCriteria, criteria],
  );

  const handleClearFilters = useCallback(() => {
    applyCriteria({ ...EMPTY_DISCOVERY_CRITERIA });
  }, [applyCriteria]);

  const handleBackToResults = useCallback(() => {
    const restaurantId = selectedId;
    setSelectedId(null);
    requestAnimationFrame(() => {
      const previousResult = restaurantId
        ? resultButtonRefs.current.get(restaurantId)
        : undefined;
      (previousResult ?? searchInputRef.current)?.focus();
    });
  }, [selectedId]);

  useEffect(() => {
    if (configurationIssue || !containerRef.current) {
      return;
    }

    mountedRef.current = true;
    setLoadStatus("loading");
    const container = containerRef.current;
    const initialization =
      initializationRef.current ??
      initializeMapRuntime({
        container,
        apiKey,
        mapId,
        mapInput,
        onSelect: handleSelect,
      });
    initializationRef.current = initialization;

    void initialization
      .then((runtime) => {
        if (mountedRef.current) {
          runtimeRef.current = runtime;
          setLoadStatus("ready");
        }
      })
      .catch(() => {
        if (mountedRef.current) {
          setLoadStatus("error");
        }
      });

    return () => {
      mountedRef.current = false;
      // Strict Mode immediately remounts effects. Deferring cleanup by one
      // microtask preserves the single initialized map during that dev probe.
      queueMicrotask(() => {
        if (!mountedRef.current) {
          void initialization
            .then((runtime) => {
              if (!mountedRef.current) {
                disposeMapRuntime(runtime);
                if (runtimeRef.current === runtime) {
                  runtimeRef.current = null;
                }
              }
            })
            .catch(() => undefined);
        }
      });
    };
  }, [apiKey, configurationIssue, handleSelect, mapId, mapInput]);

  useEffect(() => {
    if (loadStatus === "ready" && runtimeRef.current) {
      applySelection(runtimeRef.current, selectedId);
    }
  }, [loadStatus, selectedId]);

  useEffect(() => {
    if (loadStatus === "ready" && runtimeRef.current) {
      applyVisibleMarkers(runtimeRef.current, visibleRestaurantIds);
    }
  }, [loadStatus, visibleRestaurantIds]);

  useEffect(() => {
    if (!selectedId || !window.matchMedia("(max-width: 959px)").matches) {
      return;
    }

    const frame = requestAnimationFrame(() => mobileBackButtonRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [selectedId]);

  return (
    <section className={styles.experience} aria-label="Paris restaurant map">
      <div
        ref={containerRef}
        className={styles.mapCanvas}
        aria-label={`Map showing ${filteredRestaurants.length} Paris restaurants`}
      />

      <header className={styles.brandPanel}>
        <p className={styles.eyebrow}>Private collection</p>
        <h1>Paris Restaurant Map</h1>
        <div className={styles.datasetStatus}>
          <span>{restaurants.length} validated restaurants</span>
          <span aria-hidden="true">·</span>
          <span>20 arrondissements</span>
        </div>
        {usingDemoMapId ? <p className={styles.demoBadge}>Local demo map style</p> : null}
      </header>

      <aside
        className={`${styles.discoveryPanel} ${selectedRestaurant ? styles.discoveryPanelHiddenMobile : ""}`}
        aria-labelledby="discovery-heading"
      >
        <div className={styles.discoveryControls}>
          <div className={styles.discoveryHeadingRow}>
            <h2 id="discovery-heading">Find a restaurant</h2>
            {criteriaActive ? (
              <button
                type="button"
                className={styles.clearButton}
                onClick={handleClearFilters}
              >
                Clear all
              </button>
            ) : null}
          </div>

          <div className={styles.fieldGroup}>
            <label htmlFor="restaurant-search">Search</label>
            <input
              ref={searchInputRef}
              id="restaurant-search"
              type="search"
              value={criteria.search}
              onChange={handleSearchChange}
              placeholder="Name, address, or postcode"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className={styles.fieldGroup}>
            <label htmlFor="arrondissement-filter">Arrondissement</label>
            <select
              id="arrondissement-filter"
              value={criteria.arrondissement ?? ""}
              onChange={handleArrondissementChange}
            >
              <option value="">Any arrondissement</option>
              {ARRONDISSEMENTS.map((arrondissement) => (
                <option key={arrondissement} value={arrondissement}>
                  Paris {arrondissement}
                </option>
              ))}
            </select>
          </div>

          <p
            className={styles.resultCount}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            {filteredRestaurants.length} {filteredRestaurants.length === 1 ? "result" : "results"}
          </p>
        </div>

        {filteredRestaurants.length > 0 ? (
          <ol className={styles.resultList} aria-label="Restaurant results">
            {filteredRestaurants.map((restaurant) => {
              const isSelected = restaurant.id === selectedId;
              return (
                <li key={restaurant.id}>
                  <button
                    ref={(element) => {
                      if (element) {
                        resultButtonRefs.current.set(restaurant.id, element);
                      } else {
                        resultButtonRefs.current.delete(restaurant.id);
                      }
                    }}
                    type="button"
                    className={styles.resultButton}
                    data-selected={String(isSelected)}
                    aria-current={isSelected ? "true" : undefined}
                    onClick={() => handleSelect(restaurant.id)}
                  >
                    <span className={styles.resultName}>{restaurant.name}</span>
                    <span className={styles.resultMeta}>
                      <span>{formatListArrondissement(restaurant.arrondissement)}</span>
                      <span aria-hidden="true">·</span>
                      <span>{restaurant.postalCode}</span>
                    </span>
                    <span className={styles.resultAddress}>
                      {restaurant.address ?? "Address unavailable"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        ) : (
          <div className={styles.emptyResults}>
            <h3>No restaurants found</h3>
            <p>Try a different search or arrondissement.</p>
            <button type="button" className={styles.emptyClearButton} onClick={handleClearFilters}>
              Clear all filters
            </button>
          </div>
        )}
      </aside>

      {configurationIssue ? (
        <div className={styles.stateOverlay} role="status">
          <div className={styles.stateCard}>
            <p className={styles.stateLabel}>Map configuration</p>
            <h2>{configurationIssue.title}</h2>
            <p>{configurationIssue.message}</p>
            <p>The validated 300-restaurant dataset is ready.</p>
          </div>
        </div>
      ) : loadStatus === "loading" ? (
        <div className={styles.loadingStatus} role="status" aria-live="polite">
          Loading the Paris map…
        </div>
      ) : loadStatus === "error" ? (
        <div className={styles.stateOverlay} role="alert">
          <div className={styles.stateCard}>
            <p className={styles.stateLabel}>Map unavailable</p>
            <h2>Google Maps could not load</h2>
            <p>
              Check the API key restrictions, Maps JavaScript API access, billing, and
              network connection, then refresh the page.
            </p>
          </div>
        </div>
      ) : selectedRestaurant && detail ? (
        <aside className={styles.detailPanel} aria-labelledby="restaurant-name" aria-live="polite">
          <button
            ref={mobileBackButtonRef}
            type="button"
            className={styles.backButton}
            onClick={handleBackToResults}
          >
            <span aria-hidden="true">←</span> Back to results
          </button>
          <button
            type="button"
            className={styles.closeButton}
            onClick={() => setSelectedId(null)}
            aria-label="Close restaurant details"
          >
            <span aria-hidden="true">×</span>
          </button>
          <p className={styles.detailEyebrow}>{detail.arrondissement}</p>
          <h2 id="restaurant-name">{selectedRestaurant.name}</h2>
          <p className={styles.address}>{detail.address}</p>

          {detail.cuisine ? (
            <section className={styles.detailSection} aria-labelledby="cuisine-heading">
              <h3 id="cuisine-heading">Cuisine</h3>
              <ul className={styles.tagList}>
                {detail.cuisine.map((tag) => (
                  <li key={tag}>{tag}</li>
                ))}
              </ul>
            </section>
          ) : null}

          {detail.vibe ? (
            <section className={styles.detailSection} aria-labelledby="vibe-heading">
              <h3 id="vibe-heading">Vibe</h3>
              <ul className={styles.tagList}>
                {detail.vibe.map((tag) => (
                  <li key={tag}>{tag}</li>
                ))}
              </ul>
            </section>
          ) : null}

          {detail.websiteUrl || detail.instagramUrl ? (
            <nav className={styles.externalLinks} aria-label="Restaurant links">
              {detail.websiteUrl ? (
                <a href={detail.websiteUrl} target="_blank" rel="noopener noreferrer">
                  Website <span aria-hidden="true">↗</span>
                </a>
              ) : null}
              {detail.instagramUrl ? (
                <a href={detail.instagramUrl} target="_blank" rel="noopener noreferrer">
                  Instagram <span aria-hidden="true">↗</span>
                </a>
              ) : null}
            </nav>
          ) : null}
        </aside>
      ) : null}
    </section>
  );
}
