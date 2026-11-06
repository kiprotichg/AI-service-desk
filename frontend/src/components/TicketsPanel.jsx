import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";

const FILTERS = ["", "open", "in_progress", "resolved"];

/** Text cell that saves when you press Enter or leave it, and only if it changed. */
function EditableCell({ value, label, maxLength, onSave }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const commit = () => { if (draft.trim() !== value) onSave(draft.trim()); };
  return (
    <input className={`cell-input ${value ? "" : "is-missing"}`} value={draft}
      maxLength={maxLength} placeholder="Missing" aria-label={label}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") { setDraft(value); e.currentTarget.blur(); }
      }} />
  );
}

export default function TicketsPanel() {
  const [tickets, setTickets] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api.listTickets(filter)
      .then((t) => { setTickets(t); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filter]);

  useEffect(load, [load]);

  async function update(id, changes) {
    try {
      const updated = await api.updateTicket(id, changes);
      setTickets((ts) => ts.map((t) => (t.id === id ? updated : t))
        .filter((t) => !filter || t.status === filter));
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section className="card">
      <div className="panel-head">
        <h2>Support tickets</h2>
        <div className="row">
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by status">
            {FILTERS.map((f) => <option key={f} value={f}>{f ? f.replace("_", " ") : "All"}</option>)}
          </select>
          <button className="ghost" onClick={load}>Refresh</button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {loading ? <p className="muted">Loading…</p> : tickets.length === 0 ? (
        <p className="muted">No tickets yet. Escalated chats show up here.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th><th>Subject</th><th>Module</th><th>Equipment serial</th>
                <th>Customer site</th><th>Priority</th><th>Status</th><th>Created</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => {
                const service = t.module !== "sales";
                return (
                  <tr key={t.id}>
                    <td>{t.id}</td>
                    <td title={t.summary}>{t.subject}</td>
                    <td><span className={`module-tag module-${t.module}`}>
                      {service ? "Service" : "Sales"}</span></td>
                    <td>
                      {service ? (
                        <EditableCell value={t.serial_number} maxLength={100}
                          label={`Equipment serial for ticket ${t.id}`}
                          onSave={(v) => update(t.id, { serial_number: v })} />
                      ) : <span className="muted">-</span>}
                    </td>
                    <td>
                      {service ? (
                        <EditableCell value={t.customer_site} maxLength={200}
                          label={`Customer site for ticket ${t.id}`}
                          onSave={(v) => update(t.id, { customer_site: v })} />
                      ) : <span className="muted">-</span>}
                    </td>
                    <td>
                      <select value={t.priority} className={`prio-${t.priority}`}
                        onChange={(e) => update(t.id, { priority: e.target.value })}
                        aria-label={`Priority for ticket ${t.id}`}>
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                      </select>
                    </td>
                    <td>
                      <select value={t.status} onChange={(e) => update(t.id, { status: e.target.value })}
                        aria-label={`Status for ticket ${t.id}`}>
                        <option value="open">open</option>
                        <option value="in_progress">in progress</option>
                        <option value="resolved">resolved</option>
                      </select>
                    </td>
                    <td>{new Date(t.created_at).toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
