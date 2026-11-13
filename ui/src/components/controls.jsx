import { useId } from "react";

export function Slider({ label, value, min, max, step = 1, unit = "", format, onChange, hint, marker, disabled, accent }) {
  const pct = ((value - min) / (max - min)) * 100;
  const markerPct = marker == null ? null : Math.max(0, Math.min(100, ((marker - min) / (max - min)) * 100));
  const shown = format ? format(value) : `${value}${unit}`;
  return (
    <label className={`slider ${disabled ? "is-disabled" : ""}`}>
      <span className="slider-head">
        <span className="slider-label">{label}</span>
        <span className={`slider-value ${accent ? "is-accent" : ""}`}>{shown}</span>
      </span>
      <span className="slider-track">
        {markerPct != null && <span className="slider-marker" style={{ left: `${markerPct}%` }} />}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          style={{ "--pct": `${pct}%` }}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </span>
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function Toggle({ checked, onChange, label, hint, disabled }) {
  return (
    <div className={`toggle-row ${disabled ? "is-disabled" : ""}`}>
      <span className="toggle-text">
        <span>{label}</span>
        {hint && <span className="hint">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        className={`switch ${checked ? "is-on" : ""}`}
        onClick={() => onChange(!checked)}
      >
        <span />
      </button>
    </div>
  );
}

export function Select({ label, value, options, onChange, hint, icon: Icon, placeholder = "未选择", warn }) {
  const id = useId();
  const known = options.some((o) => o.value === value);
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {Icon && <Icon size={15} />}
        {label}
      </label>
      <div className={`select-wrap ${warn ? "is-warn" : ""}`}>
        <select id={id} value={value || ""} onChange={(e) => onChange(e.target.value)}>
          <option value="">{placeholder}</option>
          {value && !known && <option value={value}>{value}（未连接）</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {hint && <span className={`hint ${warn ? "is-warn" : ""}`}>{hint}</span>}
    </div>
  );
}

export function Segmented({ value, options, onChange }) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={value === o.value ? "is-active" : ""}
          onClick={() => onChange(o.value)}
        >
          <span className="seg-title">{o.label}</span>
          {o.sub && <span className="seg-sub">{o.sub}</span>}
        </button>
      ))}
    </div>
  );
}

export function Meter({ label, db, active }) {
  const pct = Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
  return (
    <div className="meter">
      <span className="meter-label">{label}</span>
      <span className="meter-bar">
        <span className={`meter-fill ${active ? "" : "is-idle"}`} style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}
