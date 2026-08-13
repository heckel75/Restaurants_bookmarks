"use client";

import { importLibrary, setOptions } from "@googlemaps/js-api-loader";

export type GoogleMapsLibraries = {
  core: google.maps.CoreLibrary;
  maps: google.maps.MapsLibrary;
  marker: google.maps.MarkerLibrary;
};

let loaderConfiguration: string | null = null;
let librariesPromise: Promise<GoogleMapsLibraries> | null = null;

export function loadGoogleMapsLibraries(apiKey: string, mapId: string) {
  const configuration = `${apiKey}\u0000${mapId}`;
  if (librariesPromise) {
    if (loaderConfiguration !== configuration) {
      return Promise.reject(
        new Error("Google Maps was already configured with different options."),
      );
    }
    return librariesPromise;
  }

  loaderConfiguration = configuration;
  setOptions({
    key: apiKey,
    mapIds: [mapId],
    v: "weekly",
  });
  librariesPromise = Promise.all([
    importLibrary("core"),
    importLibrary("maps"),
    importLibrary("marker"),
  ]).then(([core, maps, marker]) => ({ core, maps, marker }));

  return librariesPromise;
}
