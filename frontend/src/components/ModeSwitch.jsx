import { GlobeIcon, HeadsetIcon, TrendIcon } from "./Icons.jsx";

export const MODE_INFO = {
  service: { label: "Service",
             hint: "Service module: service requests, technicians, contracts, warranties",
             Icon: HeadsetIcon },
  sales: { label: "Sales", hint: "Sales module: quotations, orders, invoices, pricing, reports",
           Icon: TrendIcon },
  general: { label: "General", hint: "Ask about anything", Icon: GlobeIcon },
};

/** Switch between the Service, Sales and General assistants. */
export default function ModeSwitch({ value, onChange }) {
  return (
    <div className="mode-switch" role="radiogroup" aria-label="Answer mode">
      {Object.entries(MODE_INFO).map(([key, { label, hint, Icon }]) => (
        <button key={key} type="button" role="radio" aria-checked={value === key}
          className={value === key ? "active" : ""} onClick={() => onChange(key)}
          title={`${label}: ${hint}`}>
          <Icon width={16} height={16} />
          <span className="mode-label">{label}</span>
        </button>
      ))}
    </div>
  );
}
