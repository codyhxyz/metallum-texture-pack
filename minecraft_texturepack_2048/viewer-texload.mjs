// Shared texture loading for the Metallum viewer (lab + block modes).
import * as THREE from "three";

let maxAniso = 8;

/** Call once with the renderer's capability before loading textures. */
export function configureTexLoader({ maxAnisotropy }) {
  maxAniso = Math.min(8, maxAnisotropy || 8);
}

const loader = new THREE.ImageBitmapLoader();
loader.setOptions({
  premultiplyAlpha: "none",
  colorSpaceConversion: "none",
  imageOrientation: "flipY",
});

/**
 * Load a LabPBR map. isColor=true for albedo (sRGB), false for data maps
 * (normal / specular), which must stay linear.
 */
export function loadTexture(url, isColor) {
  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (image) => {
        const tex = new THREE.Texture(image);
        tex.flipY = false; // ImageBitmapLoader already flipped the pixels.
        tex.colorSpace = isColor ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
        tex.anisotropy = maxAniso;
        tex.needsUpdate = true;
        resolve(tex);
      },
      undefined,
      reject
    );
  });
}
