"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { api, ApiError } from "@/lib/api";
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
  const groupRef = useRef<THREE.Group | null>(null);
  const exaggerationRef = useRef(2);
  const [mesh, setMesh] = useState<TerrainMesh | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [exaggeration, setExaggeration] = useState(2);

  useEffect(() => {
    exaggerationRef.current = exaggeration;
    if (groupRef.current) {
      groupRef.current.scale.y = exaggeration;
    }
  }, [exaggeration]);

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    setMesh(null);
    setLoadError(null);
    setSceneError(null);
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
    camera.position.set(extent * 0.9, Math.max(extent * 0.58, (mesh.max_elevation_m - mesh.min_elevation_m) * 1.8), extent * 1.05);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.setAttribute("aria-label", label || `${body} 3D terrain`);
    renderer.domElement.setAttribute("role", "img");
    mount.appendChild(renderer.domElement);

    const ambient = new THREE.HemisphereLight(0xdfefff, 0x131a1e, 1.05);
    const key = new THREE.DirectionalLight(0xffead6, 2.1);
    key.position.set(extent * 0.7, extent * 0.95, extent * 0.45);
    const rim = new THREE.DirectionalLight(0x62d7e4, 0.38);
    rim.position.set(-extent * 0.8, extent * 0.3, -extent * 0.7);
    scene.add(ambient, key, rim);

    const rows = mesh.heights.length;
    const cols = mesh.heights[0]?.length ?? 0;
    const positions = new Float32Array(rows * cols * 3);
    const colors = new Float32Array(rows * cols * 3);
    const valid = new Uint8Array(rows * cols);
    const center = mesh.mean_elevation_m;
    const range = Math.max(mesh.max_elevation_m - mesh.min_elevation_m, 1e-6);
    const dx = extent / Math.max(cols - 1, 1);
    const dz = extent / Math.max(rows - 1, 1);

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
        const color = ramp((value - mesh.min_elevation_m) / range);
        colors[index * 3] = color[0];
        colors[index * 3 + 1] = color[1];
        colors[index * 3 + 2] = color[2];
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
    geometry.setIndex(indices);
    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.94,
      metalness: 0,
      side: THREE.DoubleSide,
    });
    const terrain = new THREE.Mesh(geometry, material);
    const group = new THREE.Group();
    group.add(terrain);
    group.scale.y = exaggerationRef.current;
    groupRef.current = group;
    scene.add(group);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.enablePan = false;
    controls.minDistance = extent * 0.35;
    controls.maxDistance = extent * 3.5;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.target.set(0, 0, 0);

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

    const render = () => {
      frame = requestAnimationFrame(render);
      controls.update();
      renderer.render(scene, camera);
    };
    render();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      groupRef.current = null;
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [mesh, body, label]);

  const loading = !mesh && !loadError;
  const error = loadError || sceneError;

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
            <span className="terrain-badge">REAL DEM MESH</span>
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
          </div>
          <div className="terrain-hint">drag to orbit · scroll to zoom</div>
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
            <span className="terrain-source-note">
              {mesh.note}
            </span>
          </div>
        </>
      ) : null}
    </div>
  );
}
