import { JarvisOrb } from "./JarvisOrb";
import { toolLabel, type RuntimeState } from "../runtime";
import "./TaskHud.css";

export function TaskHud({ state, cancel, restore, moving }: {
  state: RuntimeState; cancel: () => void; restore: () => void; moving: boolean;
}) {
  const task = state.task;
  const tool = task.active_tool || "";
  return (
    <main className="task-hud" aria-label="Выполнение задачи Jarvis">
      <div className="task-hud-orb"><JarvisOrb active phase={task.phase} paused={!moving} /></div>
      <div className="task-hud-copy" role="status" aria-live="polite">
        <strong>Jarvis</strong>
        <span>{task.mode === "simulation" ? "Симуляция" : "Выполнение"} · Шаг {task.steps.length + 1}</span>
        <p title={tool ? toolLabel(tool) : "Обдумываю следующий шаг"}>
          {task.phase === "yielding" ? "Пауза: жду ваших действий..."
            : task.phase === "executing" && tool ? toolLabel(tool) : "Обдумываю следующий шаг"}
        </p>
      </div>
      <div className="task-hud-actions">
        <button className="task-hud-stop" onClick={cancel} aria-label="Остановить задачу">Stop</button>
        <button className="task-hud-restore" onClick={restore} aria-label="Развернуть Jarvis">Открыть</button>
      </div>
    </main>
  );
}
