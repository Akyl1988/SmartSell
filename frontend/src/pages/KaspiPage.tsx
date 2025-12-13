// --- frontend/src/pages/KaspiPage.tsx ---
import React, { useState } from "react";

const API = "http://127.0.0.1:8000/api/v1/kaspi";

export default function KaspiPage() {
  const [log, setLog] = useState<string>("");

  async function call(method: "GET"|"POST", path: string) {
    setLog((prev) => prev + `\n> ${method} ${path}`);
    const res = await fetch(`${API}${path}`, { method });
    const txt = await res.text();
    setLog((prev) => prev + `\n${txt}\n`);
  }

  return (
    <div style={{maxWidth: 900, margin: "24px auto", fontFamily: "Inter, system-ui, Arial"}}>
      <h1>Kaspi: управление фидом</h1>
      <p>Эти кнопки бьют в наш бэкенд FastAPI, который под капотом запускает тот же адаптер.</p>

      <div style={{display:"grid", gap:12, gridTemplateColumns:"repeat(auto-fit, minmax(240px, 1fr))", margin:"16px 0"}}>
        <button onClick={() => call("GET", "/_debug/ping")}>Пинг модуля</button>
        <button onClick={() => call("GET", `/health/MyKaspiShop`)}>Проверка health</button>
        <button onClick={() => call("POST", `/feed/generate`)}>Сгенерировать фид (локально)</button>
        <button onClick={() => call("POST", `/feed/upload`)}>Загрузить фид в Kaspi</button>
        <button onClick={() => call("GET", `/import/status`)}>Статус импорта</button>
      </div>

      <pre style={{whiteSpace:"pre-wrap", background:"#111", color:"#ddd", padding:12, borderRadius:8, minHeight:180}}>
        {log || "Логи появятся здесь…"}
      </pre>
    </div>
  );
}
