import { useEffect, useState } from "react";
import type { CloudReview } from "../runtime";

export function CloudConsentCard({ review, busy, onDecision }: {
  review: CloudReview;
  busy: boolean;
  onDecision: (current: boolean, history: boolean) => Promise<void>;
}) {
  const [current, setCurrent] = useState(false);
  const [history, setHistory] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const expired = now / 1000 >= review.expires_at;
  return (
    <section className="glass-card cloud-review-card" aria-label="Вопрос о данных задачи">
      <h3>Можно передать данные этой задачи в OpenAI?</h3>
      <p className="cloud-review-command">{review.command}</p>
      <p>{review.mode === "simulation" ? "Симуляция" : "Выполнение"} · {review.reasons.join(" · ")}</p>
      <label className="check-row">
        <input type="checkbox" checked={current} disabled={busy || expired}
          onChange={(event) => setCurrent(event.target.checked)} />
        <span>Разрешаю передать эту команду, уточнения, наблюдения, содержимое
          прочитанных файлов, скриншоты, рабочие заметки, выбранные метки памяти и профиль пространства
          в OpenAI для этой задачи. API оплачивается отдельно.</span>
      </label>
      {review.history_available && <label className="check-row">
        <input type="checkbox" checked={history} disabled={busy || expired}
          onChange={(event) => setHistory(event.target.checked)} />
        <span>Также разрешаю передать историю прошлых задач: команды, заметки,
          адреса сайтов, пути и подходящие рецепты.
          Это необязательно.</span>
      </label>}
      <small>Согласие действует только для показанной задачи.
        Оно не подтверждает отправку сообщений, оплату или удаление данных.</small>
      {expired && <p role="status">Вопрос истёк. Запустите задачу заново.</p>}
      <div className="control-actions">
        <button className="outline-button" disabled={busy}
          onClick={() => void onDecision(false, false)}>Отмена</button>
        <button className="primary-button" disabled={busy || expired || !current}
          onClick={() => void onDecision(current, history)}>Продолжить задачу</button>
      </div>
    </section>
  );
}
