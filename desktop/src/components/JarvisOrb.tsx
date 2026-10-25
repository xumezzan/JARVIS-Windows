import { useEffect, useRef } from "react";
import "./JarvisOrb.css";

export interface JarvisOrbProps {
  active?: boolean;
  phase?: string;
  paused?: boolean;
  className?: string;
}

type RGB = readonly [number, number, number];
type Palette = { blue: RGB; cyan: RGB; white: RGB; deep: RGB };

const TAU = Math.PI * 2;
const STRANDS = 112;
const POINTS = 240;

function parseColor(value: string): RGB | undefined {
  const hex = value.match(/^#([\da-f]{6})$/i)?.[1];
  if (hex)
    return [
      Number.parseInt(hex.slice(0, 2), 16),
      Number.parseInt(hex.slice(2, 4), 16),
      Number.parseInt(hex.slice(4, 6), 16),
    ];
  const channels = value.match(
    /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/,
  );
  if (channels)
    return [Number(channels[1]), Number(channels[2]), Number(channels[3])];
  return undefined;
}

function rgba(color: RGB, alpha: number): string {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

/** Procedural filaments: no image, video, network access, or WebGL dependency. */
export function JarvisOrb({
  active = true,
  phase = "idle",
  paused = false,
  className = "",
}: JarvisOrbProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d", { alpha: true });
    if (!host || !canvas || !context) return;

    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let previousTime = 0;
    let elapsed = 0;
    let size = 0;
    let pixelRatio = 1;
    let palette: Palette | undefined;
    let disposed = false;

    const draw = (time: number) => {
      if (!size || !palette) return;
      const { blue, cyan, white, deep } = palette;
      const radius = size * 0.393;
      const strength = active ? 1 : 0.69;
      const breath = 1 + Math.sin(time * 0.66) * 0.009;

      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.clearRect(0, 0, size, size);
      context.translate(size / 2, size / 2);
      context.scale(breath, breath);

      // Broad atmospheric light is intentionally transparent through the center.
      const atmosphere = context.createRadialGradient(
        0,
        0,
        radius * 0.37,
        0,
        0,
        radius * 1.4,
      );
      atmosphere.addColorStop(0, rgba(deep, 0));
      atmosphere.addColorStop(0.26, rgba(deep, 0.05 * strength));
      atmosphere.addColorStop(0.51, rgba(blue, 0.28 * strength));
      atmosphere.addColorStop(0.64, rgba(blue, 0.075 * strength));
      atmosphere.addColorStop(1, rgba(blue, 0));
      context.fillStyle = atmosphere;
      context.fillRect(-size / 2, -size / 2, size, size);

      const shell = context.createRadialGradient(
        -radius * 0.21,
        -radius * 0.24,
        radius * 0.2,
        0,
        0,
        radius * 1.03,
      );
      shell.addColorStop(0, rgba(deep, 0.025 * strength));
      shell.addColorStop(0.61, rgba(deep, 0.055 * strength));
      shell.addColorStop(0.91, rgba(blue, 0.105 * strength));
      shell.addColorStop(1, rgba(blue, 0));
      context.fillStyle = shell;
      context.beginPath();
      context.arc(0, 0, radius * 1.03, 0, TAU);
      context.fill();

      context.globalCompositeOperation = "lighter";
      context.lineCap = "round";
      context.lineJoin = "round";

      // Quiet rear meridians give the hollow, loosely spherical body its depth.
      for (let index = 0; index < 22; index += 1) {
        const phase = (index / 22) * TAU + time * 0.046;
        context.beginPath();
        for (let point = 0; point <= POINTS; point += 1) {
          const angle = (point / POINTS) * TAU;
          const twist = Math.sin(angle * 2 + phase + time * 0.12) * 0.21;
          const x = radius * 0.89 * Math.cos(angle + twist);
          const y =
            radius *
            (Math.sin(angle) * Math.cos(phase) * 0.69 +
              Math.cos(angle) * Math.sin(phase) * 0.35);
          if (point === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }
        context.strokeStyle = rgba(blue, 0.039 * strength);
        context.lineWidth = size / 850;
        context.stroke();
      }

      const paths: { path: Path2D; phase: number; index: number }[] = [];
      for (let index = 0; index < STRANDS; index += 1) {
        const phase = (index / (STRANDS - 1)) * TAU;
        const path = new Path2D();
        for (let point = 0; point <= POINTS; point += 1) {
          const angle = (point / POINTS) * TAU;
          const wave =
            Math.sin(angle * 3 + phase * 0.78 + time * 0.29) * 0.083 +
            Math.cos(angle * 5 - phase * 0.91 - time * 0.11) * 0.025;
          const distance = radius * (0.83 + wave + Math.cos(phase) * 0.105);
          const twist =
            angle + Math.sin(angle * 2 - phase + time * 0.17) * 0.055;
          const x = Math.cos(twist) * distance * (1 + Math.sin(phase) * 0.025);
          const y =
            Math.sin(twist) * distance * 0.958 +
            Math.sin(angle * 2 + phase) * radius * 0.018;
          // A slight tilt follows the reference's top-left to bottom-right flow.
          const tiltedX = x * 0.992 + y * 0.125;
          const tiltedY = y * 0.992 - x * 0.125;
          if (point === 0) path.moveTo(tiltedX, tiltedY);
          else path.lineTo(tiltedX, tiltedY);
        }
        path.closePath();
        paths.push({ path, phase, index });
      }

      // A small set of broad strands supplies bloom without per-strand shadows.
      for (const { path, index } of paths) {
        if (index % 6 !== 0) continue;
        context.strokeStyle = rgba(blue, 0.07 * strength);
        context.lineWidth = size * 0.032;
        context.stroke(path);
        context.strokeStyle = rgba(cyan, 0.034 * strength);
        context.lineWidth = size * 0.013;
        context.stroke(path);
      }

      const filament = context.createLinearGradient(
        -radius * 0.8,
        -radius * 0.9,
        radius * 0.85,
        radius * 0.87,
      );
      filament.addColorStop(0, rgba(blue, 0.15));
      filament.addColorStop(0.16, rgba(cyan, 0.54));
      filament.addColorStop(0.25, rgba(white, 0.94));
      filament.addColorStop(0.35, rgba(cyan, 0.41));
      filament.addColorStop(0.52, rgba(blue, 0.26));
      filament.addColorStop(0.71, rgba(cyan, 0.72));
      filament.addColorStop(0.79, rgba(white, 0.88));
      filament.addColorStop(0.91, rgba(blue, 0.43));
      filament.addColorStop(1, rgba(blue, 0.08));

      for (const { path, phase, index } of paths) {
        const isRidge = index % 13 === 0;
        context.globalAlpha =
          (0.22 + (Math.cos(phase - 0.5) + 1) * 0.16) * strength;
        context.strokeStyle = filament;
        context.lineWidth = (size / 660) * (isRidge ? 1.65 : 0.65);
        context.stroke(path);
      }

      // Fine outer contours remain blue and low contrast rather than a solid rim.
      context.globalAlpha = strength;
      for (let index = 0; index < 9; index += 1) {
        context.beginPath();
        for (let point = 0; point <= POINTS; point += 1) {
          const angle = (point / POINTS) * TAU;
          const distance =
            radius *
            (0.96 +
              index * 0.003 +
              Math.sin(angle * 3 + index * 0.14 + time * 0.1) * 0.025);
          const x = Math.cos(angle) * distance;
          const y = Math.sin(angle) * distance * 0.953;
          if (point === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }
        context.strokeStyle = rgba(blue, 0.083);
        context.lineWidth = size / 1000;
        context.stroke();
      }

      context.globalAlpha = 1;
      context.globalCompositeOperation = "source-over";
    };

    const canAnimate = () =>
      !disposed && !paused && !motion.matches && !document.hidden;
    const tick = (timestamp: number) => {
      if (!canAnimate()) {
        frame = 0;
        previousTime = 0;
        return;
      }
      // Thirty frames per second keeps the subtle motion inexpensive on laptops.
      if (!previousTime || timestamp - previousTime >= 1000 / 30) {
        elapsed += previousTime
          ? Math.min(timestamp - previousTime, 80) / 1000
          : 0;
        previousTime = timestamp;
        draw(elapsed);
      }
      frame = window.requestAnimationFrame(tick);
    };

    const reconcileAnimation = () => {
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      previousTime = 0;
      draw(elapsed);
      if (canAnimate()) frame = window.requestAnimationFrame(tick);
    };

    const resize = () => {
      const bounds = host.getBoundingClientRect();
      size = Math.min(bounds.width, bounds.height);
      pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(size * pixelRatio);
      canvas.height = Math.round(size * pixelRatio);
      const styles = getComputedStyle(host);
      const blue = parseColor(styles.getPropertyValue("--orb-blue").trim());
      const cyan = parseColor(styles.getPropertyValue("--orb-cyan").trim());
      const white = parseColor(styles.getPropertyValue("--orb-white").trim());
      const deep = parseColor(styles.getPropertyValue("--orb-deep").trim());
      palette =
        blue && cyan && white && deep ? { blue, cyan, white, deep } : undefined;
      draw(elapsed);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();
    reconcileAnimation();
    motion.addEventListener("change", reconcileAnimation);
    document.addEventListener("visibilitychange", reconcileAnimation);
    window.addEventListener("resize", resize);

    return () => {
      disposed = true;
      if (frame) window.cancelAnimationFrame(frame);
      observer.disconnect();
      motion.removeEventListener("change", reconcileAnimation);
      document.removeEventListener("visibilitychange", reconcileAnimation);
      window.removeEventListener("resize", resize);
      context.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [active, paused]);

  return (
    <div
      ref={hostRef}
      className={`jarvis-orb ${className}`}
      data-phase={phase}
      role="img"
      aria-label={
        active
          ? "Светящаяся синяя сфера Jarvis"
          : "Сфера Jarvis в режиме ожидания"
      }
    >
      <canvas ref={canvasRef} aria-hidden="true" />
    </div>
  );
}

export default JarvisOrb;
