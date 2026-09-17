// Shared preset catalog for the Metallum viewer (lab + block modes).
//
// Only textures actually shipped in minecraft_texturepack_2048/textures/ are
// listed. The v1.0 "Companion 2K showcase" pack covers four materials; every
// other historical preset referenced dev-only PNGs that were never committed,
// so they are deliberately absent — a preset that 404s is worse than no preset.
export const PRESETS = {
  metallum_stone: {
    name: "Metallum · Stone",
    albedo: "textures/stone.png",
    normal: "textures/stone_n.png",
    specular: "textures/stone_s.png",
    depth: 0.06,
  },
  metallum_oak_planks: {
    name: "Metallum · Oak Planks",
    albedo: "textures/oak_planks.png",
    normal: "textures/oak_planks_n.png",
    specular: "textures/oak_planks_s.png",
    depth: 0.025,
  },
  metallum_iron_block: {
    name: "Metallum · Iron Block",
    albedo: "textures/iron_block.png",
    normal: "textures/iron_block_n.png",
    specular: "textures/iron_block_s.png",
    depth: 0.03,
  },
  metallum_glowstone: {
    name: "Metallum · Glowstone",
    albedo: "textures/glowstone.png",
    normal: "textures/glowstone_n.png",
    specular: "textures/glowstone_s.png",
    depth: 0.05,
  },
};

export const PRESET_ORDER = [
  "metallum_stone",
  "metallum_oak_planks",
  "metallum_iron_block",
  "metallum_glowstone",
];

export const DEFAULT_PRESET = "metallum_stone";
