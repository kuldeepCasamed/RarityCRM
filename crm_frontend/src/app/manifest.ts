import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "Rarity CRM",
    short_name: "Rarity CRM",
    description: "Rarity Dental lead, pipeline and patient CRM",
    start_url: "/dashboard",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#f7f4ef",
    theme_color: "#8c7864",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
