import type { RGB } from "./api";

export const toHex = ([r, g, b]: RGB) =>
  "#" + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

export function fromHex(hex: string): RGB | null {
  const h = hex.trim().replace(/^#/, "");
  const full = h.length === 3 ? h.split("").map((x) => x + x).join("") : h;
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as RGB;
}

// The bulb's white temperature is 0-100 (0 = 2700 K warm, 100 = 6500 K cool).
export const tempToKelvin = (t: number) => Math.round(2700 + (t / 100) * 3800);

// Approximate on-screen color of a blackbody at `kelvin` (Tanner Helland's fit).
export function kelvinToRgb(kelvin: number): RGB {
  const t = kelvin / 100;
  const clamp = (v: number) => Math.max(0, Math.min(255, v));
  const r = t <= 66 ? 255 : clamp(329.698727446 * Math.pow(t - 60, -0.1332047592));
  const g =
    t <= 66
      ? clamp(99.4708025861 * Math.log(t) - 161.1195681661)
      : clamp(288.1221695283 * Math.pow(t - 60, -0.0755148492));
  const b = t >= 66 ? 255 : t <= 19 ? 0 : clamp(138.5177312231 * Math.log(t - 10) - 305.0447927307);
  return [Math.round(r), Math.round(g), Math.round(b)];
}

export const tempToRgb = (t: number) => kelvinToRgb(tempToKelvin(t));

export const WHITE_PRESETS: { name: string; temp: number }[] = [
  { name: "Candlelight", temp: 0 },
  { name: "Warm", temp: 15 },
  { name: "Soft white", temp: 30 },
  { name: "Neutral", temp: 50 },
  { name: "Daylight", temp: 75 },
  { name: "Cool", temp: 100 },
];

export const COLOR_PRESETS: { name: string; rgb: RGB }[] = [
  { name: "Red", rgb: [255, 0, 0] },
  { name: "Coral", rgb: [255, 80, 60] },
  { name: "Orange", rgb: [255, 128, 0] },
  { name: "Amber", rgb: [255, 170, 0] },
  { name: "Yellow", rgb: [255, 230, 0] },
  { name: "Lime", rgb: [150, 255, 0] },
  { name: "Green", rgb: [0, 255, 0] },
  { name: "Mint", rgb: [0, 255, 140] },
  { name: "Teal", rgb: [0, 200, 170] },
  { name: "Cyan", rgb: [0, 255, 255] },
  { name: "Sky", rgb: [0, 150, 255] },
  { name: "Blue", rgb: [0, 0, 255] },
  { name: "Indigo", rgb: [75, 0, 255] },
  { name: "Purple", rgb: [140, 0, 255] },
  { name: "Violet", rgb: [200, 0, 255] },
  { name: "Magenta", rgb: [255, 0, 255] },
  { name: "Hot pink", rgb: [255, 0, 140] },
  { name: "Pink", rgb: [255, 105, 180] },
  { name: "Rose", rgb: [255, 60, 100] },
  { name: "Peach", rgb: [255, 150, 100] },
  { name: "Lavender", rgb: [180, 140, 255] },
  { name: "Ice", rgb: [170, 230, 255] },
  { name: "Seafoam", rgb: [120, 255, 200] },
  { name: "White", rgb: [255, 255, 255] },
];

/** What the bulb looks like right now, for swatches and glows. */
export function bulbDisplayColor(b: {
  on: boolean | null;
  rgb: RGB | null;
  temp: number | null;
  mode: "color" | "white" | null;
}): RGB | null {
  if (b.on === false) return null;
  if (b.mode === "white" && b.temp !== null) return tempToRgb(b.temp);
  if (b.rgb && b.rgb.some((v) => v > 0)) return b.rgb;
  if (b.temp !== null) return tempToRgb(b.temp);
  return null;
}

export const rgbCss = (c: RGB | null, alpha = 1) =>
  c ? `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${alpha})` : `rgba(120, 120, 130, ${alpha})`;
