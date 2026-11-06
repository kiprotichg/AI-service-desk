import { useEffect, useState } from "react";
import { api } from "../api/client.js";

function BarList({ title, data }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <div className="card">
      <h3>{title}</h3>
      {entries.length === 0 ? <p className="muted">No data yet.</p> : (
        <ul className="bars">
          {entries.map(([label, n]) => (
            <li key={label}>
              <span className="bar-label">{label.replace("_", " ")}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${(n / max) * 100}%` }} />
              </span>
              <span className="bar-value">{n}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AnalyticsPanel() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.analytics().then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  return (
    <div className="analytics">
      <div className="stats">
        <div className="card stat"><span>Conversations</span><strong>{data.conversations}</strong></div>
        <div className="card stat"><span>Messages</span><strong>{data.messages}</strong></div>
        <div className="card stat">
          <span>Escalation rate</span><strong>{Math.round(data.escalation_rate * 100)}%</strong>
        </div>
      </div>
      <div className="grid">
        <BarList title="Question topics" data={data.intents} />
        <BarList title="Sentiment" data={data.sentiment} />
        <BarList title="Tickets by status" data={data.tickets} />
        <BarList title="Chat modes" data={data.modes || {}} />
      </div>
    </div>
  );
}
