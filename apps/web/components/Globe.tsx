"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { textureUrl } from "@/lib/api";

export interface GlobePin {
  lon: number;
  lat: number;
  color?: string;
  label?: string;
}

interface GlobeProps {
  body: "earth" | "moon" | "mars" | string;
  pins?: GlobePin[];
  interactive?: boolean;
  autoRotate?: boolean;
  className?: string;
}

function pinPosition(lon: number, lat: number, radius: number): THREE.Vector3 {
  const phi = ((90 - lat) * Math.PI) / 180;
  const theta = ((lon + 180) * Math.PI) / 180;
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

export function Globe({
  body,
  pins = [],
  interactive = false,
  autoRotate = true,
  className,
}: GlobeProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "unsupported">("loading");
  const pinKey = JSON.stringify(pins);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const activePins: GlobePin[] = JSON.parse(pinKey);
    let disposed = false;
    setState("loading");
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 20);
    camera.position.set(0, 0.25, 3.4);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: "high-performance",
      });
    } catch {
      // A missing WebGL context must not take down the surrounding analysis page.
      // The renderer returns the real equirectangular source texture instead.
      setState("unsupported");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    mount.appendChild(renderer.domElement);

    const ambient = new THREE.AmbientLight(0xffffff, 1.35);
    const key = new THREE.DirectionalLight(0xfff2e6, 1.55);
    key.position.set(3.2, 2.4, 2.6);
    const rim = new THREE.DirectionalLight(0x68e0d0, 0.35);
    rim.position.set(-3, -1, -2);
    scene.add(ambient, key, rim);

    const geometry = new THREE.SphereGeometry(1, 96, 64);
    const material = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.95,
      metalness: 0.02,
    });
    const sphere = new THREE.Mesh(geometry, material);
    sphere.rotation.y = -0.6;
    scene.add(sphere);

    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");
    loader.load(
      textureUrl(body),
      (texture) => {
        if (disposed) {
          texture.dispose();
          return;
        }
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
        material.map = texture;
        material.needsUpdate = true;
        setState("ready");
      },
      undefined,
      () => {
        if (!disposed) setState("error");
      },
    );

    const pinGroup = new THREE.Group();
    for (const pin of activePins) {
      const radius = 1.008;
      const position = pinPosition(pin.lon, pin.lat, radius);
      const color = new THREE.Color(pin.color || "#ff6b35");
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(0.012, 16, 12),
        new THREE.MeshBasicMaterial({ color }),
      );
      marker.position.copy(position);
      pinGroup.add(marker);

      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.019, 0.026, 32),
        new THREE.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: 0.8,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      ring.position.copy(position);
      ring.lookAt(0, 0, 0);
      pinGroup.add(ring);
    }
    sphere.add(pinGroup);

    const atmosphere = new THREE.Mesh(
      new THREE.SphereGeometry(1.025, 64, 48),
      new THREE.MeshBasicMaterial({
        color: body === "mars" ? 0xd9825b : body === "moon" ? 0x9fb2c3 : 0x4da6ff,
        transparent: true,
        opacity: 0.055,
        side: THREE.BackSide,
        depthWrite: false,
      }),
    );
    scene.add(atmosphere);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.enablePan = false;
    controls.enableZoom = interactive;
    controls.enableRotate = interactive;
    controls.autoRotate = autoRotate;
    controls.autoRotateSpeed = 0.38;
    controls.minDistance = 2.4;
    controls.maxDistance = 5.2;

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let frame = 0;
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
      geometry.dispose();
      material.map?.dispose();
      material.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [body, interactive, autoRotate, pinKey]);

  return (
    <div className={className} style={{ position: "relative", width: "100%", height: "100%" }}>
      <div ref={mountRef} style={{ position: "absolute", inset: 0 }} />
      {state === "loading" && (
        <div className="globe-loading">
          <span className="spin" />
          <span>loading {body} texture</span>
        </div>
      )}
      {state === "error" && (
        <div className="globe-loading error">
          <span>texture unavailable</span>
          <span className="faint">start the API to load NASA imagery</span>
        </div>
      )}
      {state === "unsupported" && (
        <>
          {/* Real source texture, not a synthetic placeholder. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className="globe-fallback"
            src={textureUrl(body)}
            alt={`Equirectangular ${body} source texture`}
          />
          <div className="globe-loading error">
            <span>WebGL unavailable</span>
            <span className="faint">showing the real equirectangular source texture instead</span>
          </div>
        </>
      )}
    </div>
  );
}
