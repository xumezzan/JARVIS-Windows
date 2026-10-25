import { useEffect, useState } from "react";
import { JarvisOrb } from "./JarvisOrb";
import { toolLabel } from "../runtime";
import "./CompactHud.css";

export type HudState = {
  command: string;
  phase: string;
  mode: string;
  step: number;
  tool: string;
  notification?: string;
};

export function CompactHud() {
  const [state, setState] = useState<HudState | null>(null);
  useEffect(() => {
    document.documentElement.dataset.surface = "hud";
    const api = window.jarvisOverlay;
    if (!api) return;
    let disposed = false;
    const unsubscribe = api.subscribe(setState);
    void api.status().then((next) => { if (!disposed) setState(next); });
    return () => { disposed = true; unsubscribe(); };
  }, []);
  return (
    <main className="compact-hud" aria-label={state?.notification ? "Подсказка Jarvis" : "Выполнение задачи Jarvis"} role="status">
      <div key={state?.notification || "task"} className={`compact-hud-orb ${state?.notification ? "has-notification" : ""}`}><JarvisOrb active phase={state?.phase} /></div>
      <div className="compact-hud-content">
        <div className="compact-hud-heading"><strong>Jarvis</strong><span>{state?.mode === "wake" ? "Микрофон" : state?.notification ? "Подсказка" : `Шаг ${state?.step || 1}`}</span></div>
        <p className="compact-hud-status">{state?.mode === "wake" ? "Микрофон включён" : state?.notification ? "Есть предложение" : state?.phase === "executing" && state.tool ? toolLabel(state.tool) : "Обдумываю следующий шаг"}</p>
        <p className="compact-hud-command">{state?.command || "Выполняю задачу"}</p>
        <small>{navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+Alt+J — открыть · +Esc — стоп</small>
      </div>
    </main>
  );
}
