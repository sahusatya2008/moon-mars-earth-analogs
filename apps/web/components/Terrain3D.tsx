"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { api, ApiError, apiAssetUrl } from "@/lib/api";
import type { TerrainMesh } from "@/lib/types";

const RAMP: Array<[number, [number, number, number]]> = [
  [0.0, [0.04, 0.1, 0.18]],
  [0.2, [0.08, 0.28, 0.36]],
  [0.45, [0.2, 0.48, 0.43]],
  [0.68, [0.5, 0.6, 0.44]],
  [0.84, [0.75, 0.68, 0.52]],
  [1.0, [0.96, 0.95, 0.9]],
];

function ramp(t: number): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, t));
  for (let index = 1; index < RAMP.length; index += 1) {
    const [stop, color] = RAMP[index];
    if (clamped <= stop || index === RAMP.length - 1) {
      const [prevStop, prevColor] = RAMP[index - 1];
      const span = Math.max(stop - prevStop, 1e-9);
      const mix = (clamped - prevStop) / span;
      return [
        prevColor[0] + (color[0] - prevColor[0]) * mix,
        prevColor[1] + (color[1] - prevColor[1]) * mix,
        prevColor[2] + (color[2] - prevColor[2]) * mix,
      ];
    }
  }
  return RAMP[RAMP.length - 1][1];
}

function displayResolution(sizeKm: number): number {
  const target = Math.ceil((sizeKm * 1000) / 80 / 10) * 10;
  return Math.max(120, Math.min(2000, target));
}

type TerrainView = {
  terrain: THREE.Mesh;
  elevationMaterial: THREE.MeshStandardMaterial;
  imageryMaterial: THREE.MeshStandardMaterial;
  group: THREE.Group;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  reset: () => void;
  imageryTexture: THREE.Texture | null;
};

export interface Terrain3DProps {
  body: string;
  lon: number;
  lat: number;
  sizeKm: number;
  className?: string;
  label?: string;
}

export function Terrain3D({
  body,
  lon,
  lat,
  sizeKm,
  className,
  label,
}: Terrain3DProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<TerrainView | null>(null);
  const targetExaggerationRef = useRef(2);
  const autoOrbitRef = useRef(true);
  const imageryEnabledRef = useRef(true);
  const [mesh, setMesh] = useState<TerrainMesh | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const [imageryStatus, setImageryStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [imageryEnabled, setImageryEnabled] = useState(true);
  const [autoOrbit, setAutoOrbit] = useState(true);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [exaggeration, setExaggeration] = useState(2);

  useEffect(() => {
    targetExaggerationRef.current = exaggeration;
  }, [exaggeration]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      setPrefersReducedMotion(query.matches);
      if (query.matches) setAutoOrbit(false);
    };
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    autoOrbitRef.current = autoOrbit;
    const controls = viewRef.current?.controls;
    if (controls) {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      controls.autoRotate = autoOrbit && !reduceMotion;
    }
  }, [autoOrbit]);

  useEffect(() => {
    imageryEnabledRef.current = imageryEnabled;
    const view = viewRef.current;
    if (!view) return;
    const useImagery = imageryEnabled && view.imageryTexture !== null;
    view.terrain.material = useImagery ? view.imageryMaterial : view.elevationMaterial;
  }, [imageryEnabled, imageryStatus]);

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    setMesh(null);
    setLoadError(null);
    setSceneError(null);
    setImageryStatus("idle");
    setImageryEnabled(true);
    api
      .terrain(body, lon, lat, sizeKm, controller.signal, displayResolution(sizeKm))
      .then((payload) => {
        if (live) setMesh(payload);
      })
      .catch((error) => {
        if (!live) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setLoadError(
          error instanceof ApiError
            ? error.message
            : error instanceof Error
              ? error.message
              : "Could not load the terrain model.",
        );
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [body, lon, lat, sizeKm, attempt]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !mesh) return;

    setSceneError(null);
    let disposed = false;
    let frame = 0;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: "high-performance",
      });
    } catch {
      setSceneError("WebGL is required to render the real DEM mesh in this browser.");
      return;
    }

    const scene = new THREE.Scene();
    const extent = mesh.size_km * 1000;
    const camera = new THREE.PerspectiveCamera(38, 1, extent * 0.001, extent * 20);
    const finalPosition = new THREE.Vector3(
      extent * 0.9,
      Math.max(extent * 0.58, (mesh.max_elevation_m - mesh.min_elevation_m) * 1.8),
      extent * 1.05,
    );
    const introPosition = new THREE.Vector3(
      extent * 1.45,
      Math.max(extent * 0.92, (mesh.max_elevation_m - mesh.min_elevation_m) * 2.6),
      extent * 1.65,
    );
    camera.position.copy(introPosition);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.setAttribute("aria-label", label || `${body} 3D terrain`);
    renderer.domElement.setAttribute("role", "img");
    mount.appendChild(renderer.domElement);

    const ambient = new THREE.HemisphereLight(0xdfefff, 0x131a1e, 0.62);
    const key = new THREE.DirectionalLight(0xffead6, 1.28);
    key.position.set(extent * 0.7, extent * 0.95, extent * 0.45);
    const rim = new THREE.DirectionalLight(0x62d7e4, 0.22);
    rim.position.set(-extent * 0.8, extent * 0.3, -extent * 0.7);
    scene.add(ambient, key, rim);

    const rows = mesh.heights.length;
    const cols = mesh.heights[0]?.length ?? 0;
    const positions = new Float32Array(rows * cols * 3);
    const colors = new Float32Array(rows * cols * 3);
    const uvs = new Float32Array(rows * cols * 2);
    const valid = new Uint8Array(rows * cols);
    const center = mesh.mean_elevation_m;
    const range = Math.max(mesh.max_elevation_m - mesh.min_elevation_m, 1e-6);
    const dx = extent / Math.max(cols - 1, 1);
    const dz = extent / Math.max(rows - 1, 1);
    const vertexColor = new THREE.Color();

    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const index = row * cols + col;
        const value = mesh.heights[row][col];
        if (value === null) continue;
        valid[index] = 1;
        const x = -extent / 2 + col * dx;
        const z = -extent / 2 + row * dz;
        positions[index * 3] = x;
        positions[index * 3 + 1] = value - center;
        positions[index * 3 + 2] = z;
        uvs[index * 2] = cols > 1 ? col / (cols - 1) : 0.5;
        uvs[index * 2 + 1] = rows > 1 ? 1 - row / (rows - 1) : 0.5;
        const srgb = ramp((value - mesh.min_elevation_m) / range);
        vertexColor.setRGB(srgb[0], srgb[1], srgb[2], THREE.SRGBColorSpace);
        colors[index * 3] = vertexColor.r;
        colors[index * 3 + 1] = vertexColor.g;
        colors[index * 3 + 2] = vertexColor.b;
      }
    }

    const indices: number[] = [];
    for (let row = 0; row < rows - 1; row += 1) {
      for (let col = 0; col < cols - 1; col += 1) {
        const a = row * cols + col;
        const b = row * cols + col + 1;
        const c = (row + 1) * cols + col + 1;
        const d = (row + 1) * cols + col;
        if (!valid[a] || !valid[b] || !valid[c] || !valid[d]) continue;
        indices.push(a, c, b, a, d, c);
      }
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();

    const elevationMaterial = new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.94,
      metalness: 0,
      side: THREE.DoubleSide,
    });
    const imageryMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.9,
      metalness: 0,
      side: THREE.DoubleSide,
    });
    const terrain = new THREE.Mesh(geometry, elevationMaterial);
    const group = new THREE.Group();
    group.add(terrain);
    group.scale.y = 0.001;
    scene.add(group);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.enablePan = false;
    controls.minDistance = extent * 0.35;
    controls.maxDistance = extent * 3.5;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.target.set(0, 0, 0);
    controls.autoRotateSpeed = 0.42;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Hold the orbit during the camera reveal; the render loop enables it when the
    // reveal completes and reduced motion is not requested.
    controls.autoRotate = false;

    const reset = () => {
      camera.position.copy(finalPosition);
      controls.target.set(0, 0, 0);
      controls.update();
    };

    const view: TerrainView = {
      terrain,
      elevationMaterial,
      imageryMaterial,
      group,
      camera,
      controls,
      reset,
      imageryTexture: null,
    };
    viewRef.current = view;

    let loadedImagery: THREE.Texture | null = null;
    const imageryUrl =
      mesh.imagery?.available && mesh.imagery.url ? apiAssetUrl(mesh.imagery.url) : null;
    if (imageryUrl) {
      setImageryStatus("loading");
      const loader = new THREE.TextureLoader();
      loader.setCrossOrigin("anonymous");
      loader.load(
        imageryUrl,
        (texture) => {
          if (disposed) {
            texture.dispose();
            return;
          }
          texture.colorSpace = THREE.SRGBColorSpace;
          texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
          texture.wrapS = THREE.ClampToEdgeWrapping;
          texture.wrapT = THREE.ClampToEdgeWrapping;
          texture.minFilter = THREE.LinearMipmapLinearFilter;
          texture.magFilter = THREE.LinearFilter;
          loadedImagery = texture;
          view.imageryTexture = texture;
          imageryMaterial.map = texture;
          imageryMaterial.needsUpdate = true;
          if (imageryEnabledRef.current) terrain.material = imageryMaterial;
          setImageryStatus("ready");
        },
        undefined,
        () => {
          if (!disposed) {
            terrain.material = elevationMaterial;
            setImageryStatus("error");
          }
        },
      );
    } else {
      setImageryStatus("idle");
    }

    let resumeTimer: ReturnType<typeof setTimeout> | null = null;
    const pauseOrbit = () => {
      controls.autoRotate = false;
      if (resumeTimer) {
        clearTimeout(resumeTimer);
        resumeTimer = null;
      }
    };
    const resumeOrbit = () => {
      if (reduceMotion || !autoOrbitRef.current) return;
      if (resumeTimer) clearTimeout(resumeTimer);
      resumeTimer = setTimeout(() => {
        if (!disposed && autoOrbitRef.current) controls.autoRotate = true;
      }, 2200);
    };
    controls.addEventListener("start", pauseOrbit);
    controls.addEventListener("end", resumeOrbit);

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect();
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let introActive = !reduceMotion;
    let currentExaggeration = targetExaggerationRef.current;
    const introStarted = performance.now();
    const render = (now: number) => {
      frame = requestAnimationFrame(render);
      if (introActive) {
        const progress = Math.min(1, (now - introStarted) / 1100);
        const eased = 1 - Math.pow(1 - progress, 3);
        camera.position.lerpVectors(introPosition, finalPosition, eased);
        group.scale.y = Math.max(0.001, currentExaggeration * eased);
        if (progress >= 1) {
          introActive = false;
          controls.autoRotate = autoOrbitRef.current && !reduceMotion;
        }
      } else {
        currentExaggeration +=
          (targetExaggerationRef.current - currentExaggeration) * 0.12;
        group.scale.y = Math.max(0.001, currentExaggeration);
      }
      controls.update();
      renderer.render(scene, camera);
    };
    frame = requestAnimationFrame(render);

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      if (resumeTimer) clearTimeout(resumeTimer);
      controls.removeEventListener("start", pauseOrbit);
      controls.removeEventListener("end", resumeOrbit);
      observer.disconnect();
      controls.dispose();
      if (viewRef.current === view) viewRef.current = null;
      geometry.dispose();
      elevationMaterial.dispose();
      imageryMaterial.dispose();
      loadedImagery?.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [mesh, body, label]);

  const resetView = useCallback(() => {
    viewRef.current?.reset();
  }, []);

  const loading = !mesh && !loadError;
  const error = loadError || sceneError;
  const imageryAvailable = Boolean(mesh?.imagery?.available);
  const imageryActive = imageryEnabled && imageryStatus === "ready";

  return (
    <div className={`terrain-viewer ${className || ""}`}>
      <div className="terrain-canvas" ref={mountRef} />
      {loading ? (
        <div className="globe-loading">
          <span className="spin" />
          <span>sampling real DEM geometry</span>
        </div>
      ) : null}
      {error ? (
        <div className="terrain-error">
          <b>3D terrain unavailable</b>
          <span>{error}</span>
          {loadError ? (
            <button className="btn small" type="button" onClick={() => setAttempt((value) => value + 1)}>
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
      {mesh ? (
        <>
          <div className="terrain-toolbar">
            <span className="terrain-badge">
              {imageryStatus === "loading" ? "DRAPING NASA IMAGERY" : "REAL DEM MESH"}
            </span>
            <div className="terrain-controls">
              <label className="terrain-scale">
                Vertical exaggeration
                <input
                  type="range"
                  min={1}
                  max={4}
                  step={0.5}
                  value={exaggeration}
                  onChange={(event) => setExaggeration(Number(event.target.value))}
                />
                <span>×{exaggeration.toFixed(1)}</span>
              </label>
              <button
                type="button"
                className="terrain-toggle"
                data-active={imageryActive}
                aria-pressed={imageryActive}
                disabled={!imageryAvailable || imageryStatus === "error"}
                onClick={() => setImageryEnabled((value) => !value)}
                title="Toggle the real NASA imagery drape"
              >
                NASA imagery
              </button>
              <button
                type="button"
                className="terrain-toggle"
                data-active={autoOrbit && !prefersReducedMotion}
                aria-pressed={autoOrbit && !prefersReducedMotion}
                disabled={prefersReducedMotion}
                onClick={() => setAutoOrbit((value) => !value)}
                title={
                  prefersReducedMotion
                    ? "Auto-orbit is off because your system requests reduced motion"
                    : "Toggle auto-orbit"
                }
              >
                Auto-orbit
              </button>
              <button type="button" className="terrain-toggle" onClick={resetView}>
                Reset view
              </button>
            </div>
          </div>
          <div className="terrain-hint">
            drag to orbit · scroll to zoom ·{" "}
            {imageryStatus === "loading"
              ? "loading real NASA imagery"
              : imageryStatus === "error"
                ? "imagery unavailable, showing elevation ramp"
                : imageryActive
                  ? "real NASA imagery over measured DEM"
                  : "elevation ramp"}
          </div>
          <div className="terrain-legend">
            <span>{mesh.min_elevation_m.toFixed(0)} m</span>
            <i />
            <span>{mesh.max_elevation_m.toFixed(0)} m</span>
          </div>
          <div className="terrain-source">
            <b>{mesh.source.dataset_id}</b>
            <span>
              {mesh.center.lat.toFixed(3)}°, {mesh.center.lon.toFixed(3)}° ·{" "}
              {mesh.size_km} km · {mesh.resolution_m.toFixed(0)} m sampling ·{" "}
              {(mesh.valid_fraction * 100).toFixed(1)}% valid
            </span>
            {mesh.imagery?.available ? (
              <span className="terrain-source-note">
                Imagery: {mesh.imagery.source} · {mesh.imagery.attribution}. Draped over the
                measured DEM for display only.
              </span>
            ) : null}
            <span className="terrain-source-note">{mesh.note}</span>
          </div>
        </>
      ) : null}
    </div>
  );
}
