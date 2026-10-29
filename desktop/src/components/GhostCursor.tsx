import { useEffect, useState } from "react";
import "./GhostCursor.css";

export type GhostState = {
  id: string;
  cursor_state: "moving" | "acting" | "yielding";
  target_x: number;
  target_y: number;
  action: "click" | "type" | "scroll";
  label: string;
  bounding_box: { left: number; top: number; width: number; height: number };
};

export function GhostCursor() {
  const [state, setState] = useState<GhostState | null>(null);
  const [arrived, setArrived] = useState("");
  useEffect(() => {
    document.documentElement.classList.add("ghost-surface");
    return window.jarvisGhost?.subscribe(setState);
  }, []);
  const id = state?.id;
  useEffect(() => {
    if (!id) return;
    let frame = 0, paint = 0;
    // A receipt is sent only after the complete flight and two painted frames.
    const timer = setTimeout(() => {
      setArrived(id);
      frame = requestAnimationFrame(() => {
        paint = requestAnimationFrame(() => window.jarvisGhost?.painted(id));
      });
    }, 280);
    return () => { clearTimeout(timer); cancelAnimationFrame(frame); cancelAnimationFrame(paint); };
  }, [id]);
  if (!state) return null;
  const yielding = state.cursor_state === "yielding";
  const acting = arrived === state.id && !yielding;
  const box = state.bounding_box;
  const label = yielding ? "Уступаю мышь..." : state.action === "type" ? "Ввод текста..."
    : state.action === "scroll" ? "Прокрутка" : `Клик: «${state.label || "Элемент"}»`;
  return <div className={`ghost-layer ${yielding ? "is-yielding" : ""}`}>
    {!yielding && <div key={`box-${id}`} className={`ghost-box ${acting ? "is-acting" : ""}`}
      style={{ left: box.left, top: box.top, width: box.width, height: box.height }} />}
    {acting && state.action === "click" && <div key={id} className="ghost-ripple"
      style={{ left: state.target_x, top: state.target_y }} />}
    <div className="ghost-cursor" style={{ transform:
      `translate3d(${state.target_x + (yielding ? 40 : 0)}px, ${state.target_y}px, 0)` }}>
      <svg className="ghost-glyph" width="30" height="38" viewBox="0 0 30 38" aria-hidden="true">
        <path d="M2 2 26 17 16 20 11 32Z" />
        <path className="ghost-core" d="m7 9 10 7-6 2Z" />
      </svg>
      <div className="ghost-badge" role="status">
        <span className="ghost-brand">JARVIS</span><span>{label}</span>
        {yielding && <small>Пауза: жду ваших действий...</small>}
      </div>
    </div>
  </div>;
}
