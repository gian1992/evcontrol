/**
 * EV Control Lovelace Card
 * Compatible: HA Lovelace, FullyKiosk, Android WebView 68+
 *
 * Config:
 *   type: custom:evcontrol-card
 *   entity: sensor.ev_control_charging_status
 *   # Optional: smart_switch: switch.ev_control_smart_solar_protection_anti_cycle_
 */

const EV_MODES = [
  { key: "eco",      label: "ECO"    },
  { key: "normal",   label: "NORMAL" },
  { key: "power",    label: "POWER"  },
  { key: "auto",     label: "AUTO"   },
  { key: "auto-eco", label: "A-ECO"  },
];

const DOT_COLORS = {
  solar_charging: "#2ecc71",
  hold_6a:        "#f39c12",
  smart_hold_6a:  "#e67e22",
  _off:           "#7f8c8d",
};

function dotColor(a) {
  if ((a.available_current || 0) > 0) {
    const s = a.eco_last_state || a.auto_last_state || "";
    if (s === "smart_hold_6a") return DOT_COLORS.smart_hold_6a;
    if (s === "hold_6a")       return DOT_COLORS.hold_6a;
    return DOT_COLORS.solar_charging;
  }
  return DOT_COLORS._off;
}

function fmtW(w) {
  w = w || 0;
  return Math.abs(w) >= 1000 ? (w / 1000).toFixed(1) + "kW" : Math.round(w) + "W";
}

// ── CSS (no gap property — use margin for WebView compat) ─────────────
const CSS = `
:host { display: block; -webkit-user-select: none; user-select: none; }

.card {
  background: var(--ha-card-background, var(--card-background-color, #1c1c1e));
  border-radius: var(--ha-card-border-radius, 12px);
  box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,.3));
  padding: 14px 16px 12px;
  font-family: var(--primary-font-family, sans-serif);
  color: var(--primary-text-color, #e0e0e0);
}

/* Header */
.hdr {
  display: -webkit-flex; display: flex;
  -webkit-align-items: center; align-items: center;
  -webkit-justify-content: space-between; justify-content: space-between;
  margin-bottom: 10px;
}
.title { font-size: .85rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; opacity: .7; }
.toggles { display: -webkit-flex; display: flex; }

/* Toggle buttons */
.tog {
  border: none; border-radius: 20px;
  padding: 4px 11px; margin-left: 6px;
  font-size: .72rem; font-weight: 700;
  cursor: pointer; letter-spacing: .03em;
  touch-action: manipulation;
  -webkit-tap-highlight-color: transparent;
  -webkit-transition: background .2s; transition: background .2s;
}
.tog.on   { background: #2ecc71; color: #fff; }
.tog.off  { background: #3a3a3c; color: #888; }
.tog.son  { background: #e67e22; color: #fff; }
.tog.soff { background: #3a3a3c; color: #888; }

/* Mode tabs — margin instead of gap */
.modes {
  display: -webkit-flex; display: flex;
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
  margin-bottom: 10px;
}
.modes::-webkit-scrollbar { display: none; }

.mbtn {
  -webkit-flex: 1; flex: 1; min-width: 0;
  border: 1.5px solid transparent; border-radius: 8px;
  padding: 6px 2px; margin-right: 4px;
  font-size: .65rem; font-weight: 700; letter-spacing: .03em;
  text-align: center; white-space: nowrap; cursor: pointer;
  background: #2a2a2c; color: #888;
  touch-action: manipulation;
  -webkit-tap-highlight-color: transparent;
  -webkit-transition: background .15s, border-color .15s;
  transition: background .15s, border-color .15s;
}
.mbtn:last-child { margin-right: 0; }
.mbtn.active {
  background: var(--primary-color, #03a9f4);
  border-color: var(--primary-color, #03a9f4);
  color: #fff;
}

/* Status bar */
.status {
  display: -webkit-flex; display: flex;
  -webkit-align-items: center; align-items: center;
  background: rgba(255,255,255,.05); border-radius: 8px;
  padding: 8px 10px; margin-bottom: 10px; min-height: 34px;
}
.dot {
  -webkit-flex-shrink: 0; flex-shrink: 0;
  width: 9px; height: 9px; border-radius: 50%;
  background: #7f8c8d; margin-right: 8px;
  -webkit-transition: background .3s; transition: background .3s;
}
.scol {
  display: -webkit-flex; display: flex;
  -webkit-flex-direction: column; flex-direction: column;
  min-width: 0;
}
.stxt {
  font-size: .78rem; line-height: 1.3; font-weight: 600;
  overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
/* The reason sits under the action, quieter: it is there when you look for it,
   not competing with what the charger is doing right now. */
.swhy {
  font-size: .68rem; line-height: 1.35; opacity: .62; margin-top: 2px;
  overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
}
/* The plan while it is standing still: when it starts, and by when it has to.
   Its own row because the reason above is clamped to three lines and this is
   exactly the part you do not want cut off. */
.plan {
  font-size: .68rem; line-height: 1.4;
  border-radius: 8px; padding: 7px 10px; margin-bottom: 10px;
  border-left: 3px solid var(--primary-color, #03a9f4);
  background: rgba(255,255,255,.04);
}
.plan.hide { display: none; }
.plan .pl { display: block; }
.plan .pl + .pl { margin-top: 3px; opacity: .75; }

/* Will the car be ready? Its own strip, because it is the one thing worth
   checking at a glance. */
.outlook {
  font-size: .68rem; line-height: 1.35;
  border-radius: 8px; padding: 7px 10px; margin-bottom: 10px;
  border-left: 3px solid #7f8c8d; background: rgba(255,255,255,.04);
}
.outlook.hide { display: none; }
.outlook b { font-weight: 600; }
.outlook.ok   { border-left-color: #2ecc71; }
.outlook.warn { border-left-color: #f39c12; }
.outlook.bad  { border-left-color: #e74c3c; }

/* Metrics */
.metrics { display: -webkit-flex; display: flex; }
.metric {
  -webkit-flex: 1; flex: 1;
  display: -webkit-flex; display: flex;
  -webkit-flex-direction: column; flex-direction: column;
  -webkit-align-items: center; align-items: center;
  background: rgba(255,255,255,.05); border-radius: 8px;
  padding: 6px 4px; margin-right: 4px;
}
.metric:last-child { margin-right: 0; }
.mico { font-size: .88rem; margin-bottom: 2px; }
.mval { font-size: .8rem; font-weight: 700; }
.mlbl { font-size: .6rem; opacity: .5; margin-top: 1px; }
`;

// ── HTML template (HTML entities instead of emoji for WebView safety) ──
const TMPL = `
<div class="card">
  <div class="hdr">
    <span class="title">&#x26A1; EV Control</span>
    <div class="toggles">
      <button class="tog" id="tc"></button>
      <button class="tog" id="ts"></button>
    </div>
  </div>
  <div class="modes" id="modes"></div>
  <div class="status">
    <span class="dot" id="dot"></span>
    <span class="scol">
      <span class="stxt" id="stxt">&#x2014;</span>
      <span class="swhy" id="swhy"></span>
    </span>
  </div>
  <div class="plan" id="plan"></div>
  <div class="outlook" id="outlook"></div>
  <div class="metrics">
    <div class="metric">
      <span class="mico">&#x2600;&#xFE0F;</span>
      <span class="mval" id="mpv">&#x2014;</span>
      <span class="mlbl">PV</span>
    </div>
    <div class="metric">
      <span class="mico">&#x26A1;</span>
      <span class="mval" id="msr">&#x2014;</span>
      <span class="mlbl">Surplus</span>
    </div>
    <div class="metric">
      <span class="mico">&#x1F50C;</span>
      <span class="mval" id="mcu">&#x2014;</span>
      <span class="mlbl">Corrente</span>
    </div>
    <div class="metric">
      <span class="mico">&#x1F697;</span>
      <span class="mval" id="mev">&#x2014;</span>
      <span class="mlbl">EV SOC</span>
    </div>
  </div>
</div>`;

// ── Card class — ES6 required for correct 'hass' setter behaviour ──────
class EVControlCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
  }

  // Called by HA before hass
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("evcontrol-card: 'entity' is required");
    }
    this._config = config;
  }

  // HA calls this setter on every state change — ES6 class syntax is
  // mandatory here; Object.defineProperty on prototype conflicts with
  // HA's internal property management and throws "Cannot redefine property".
  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;      // setConfig not yet called (shouldn't happen)
    if (!this._built) this._build();
    this._update();
  }

  _build() {
    this._built = true;
    const sr = this.shadowRoot;

    const style = document.createElement("style");
    style.textContent = CSS;
    sr.appendChild(style);

    const div = document.createElement("div");
    div.innerHTML = TMPL;
    sr.appendChild(div.firstElementChild);

    // Mode buttons
    const modeRow = sr.querySelector("#modes");
    EV_MODES.forEach(({ key, label }) => {
      const btn = document.createElement("button");
      btn.className = "mbtn";
      btn.dataset.mode = key;
      btn.textContent = label;
      btn.addEventListener("click", () => this._setMode(key));
      modeRow.appendChild(btn);
    });

    sr.querySelector("#tc").addEventListener("click", () => this._toggleCharge());
    sr.querySelector("#ts").addEventListener("click", () => this._toggleSmart());
  }

  _update() {
    const sr  = this.shadowRoot;
    const st  = this._hass.states[this._config.entity];
    if (!st) return;

    const a = st.attributes || {};
    const cur     = a.available_current  || 0;
    const mode    = a.mode               || "eco";
    const chargeOn = a.charging_enabled  !== false;
    const smartOn  = !!a.smart_solar_protection;
    const pv      = a.pv_power  || 0;
    const surp    = a.surplus   || 0;
    const evsoc   = a.ev_soc   || 0;
    const msg     = a.status_headline || st.state || a.status_message || "—";
    const why     = a.perche_breve || a.perche || "";
    const startAt = a.quando_parto && a.quando_parto !== "—" ? a.quando_parto : "";
    const dueBy   = a.scadenza     && a.scadenza     !== "—" ? a.scadenza     : "";
    const outlook = a.ce_la_fa || "";
    const outDet  = a.dettaglio_previsione || "";
    const color   = dotColor(a);

    // Mode tabs
    sr.querySelectorAll(".mbtn").forEach(btn => {
      btn.className = btn.dataset.mode === mode ? "mbtn active" : "mbtn";
    });

    // Charge toggle
    const tc = sr.querySelector("#tc");
    tc.textContent = chargeOn ? "CARICA ON" : "CARICA OFF";
    tc.className   = "tog " + (chargeOn ? "on" : "off");

    // Smart toggle
    const ts = sr.querySelector("#ts");
    ts.textContent = smartOn ? "SMART ON" : "SMART OFF";
    ts.className   = "tog " + (smartOn ? "son" : "soff");

    // Status
    const dot = sr.querySelector("#dot");
    dot.style.background = color;
    dot.style.boxShadow  = "0 0 8px " + color;
    sr.querySelector("#stxt").textContent = msg;
    sr.querySelector("#swhy").textContent = why;

    // Plan row: only while it is not charging, and only if there is something
    // to say about when it will be.
    const plan = sr.querySelector("#plan");
    plan.innerHTML = "";
    if (!startAt && !dueBy) {
      plan.className = "plan hide";
    } else {
      plan.className = "plan";
      [startAt, dueBy].filter(Boolean).forEach(text => {
        const line = document.createElement("span");
        line.className = "pl";
        line.textContent = text;
        plan.appendChild(line);
      });
    }

    // Outlook strip
    const out = sr.querySelector("#outlook");
    if (!outlook) {
      out.className = "outlook hide";
    } else {
      const tone = outlook === "in ritardo" ? "bad"
                 : (outlook === "completata" || outlook === "in orario") ? "ok"
                 : "warn";
      out.className = "outlook " + tone;
      out.innerHTML = "";
      const label = document.createElement("b");
      label.textContent = outlook.toUpperCase();
      out.appendChild(label);
      if (outDet) {
        out.appendChild(document.createTextNode(" — " + outDet));
      }
    }

    // Metrics
    const surpStr = (surp >= 0 ? "+" : "") + fmtW(Math.abs(surp));
    sr.querySelector("#mpv").textContent = fmtW(pv);
    sr.querySelector("#msr").textContent = surpStr;
    sr.querySelector("#mcu").textContent = cur + "A";
    sr.querySelector("#mev").textContent = evsoc > 0 ? evsoc + "%" : "—";
  }

  _setMode(mode) {
    this._hass.callService("evcontrol", "set_mode", { mode });
  }

  _toggleCharge() {
    const st  = this._hass.states[this._config.entity];
    const on  = st && st.attributes && st.attributes.charging_enabled !== false;
    this._hass.callService("evcontrol", on ? "stop_charging" : "start_charging", {});
  }

  _toggleSmart() {
    const hass = this._hass;
    const eid  = this._config.smart_switch || this._deriveSwitch();
    if (eid && hass.states[eid]) {
      hass.callService("homeassistant", "toggle", { entity_id: eid });
      return;
    }
    // Fallback: scan all states for smart_solar_protection switch
    const found = Object.keys(hass.states).find(
      id => id.startsWith("switch.") && id.includes("smart_solar_protection")
    );
    if (found) hass.callService("homeassistant", "toggle", { entity_id: found });
  }

  _deriveSwitch() {
    const base = this._config.entity
      .replace(/^sensor\./, "")
      .replace(/_charging_status$/, "")
      .replace(/_debug$/, "");
    return "switch." + base + "_smart_solar_protection_anti_cycle_";
  }

  getCardSize() { return 3; }

  static getConfigElement() {
    return document.createElement("evcontrol-card-editor");
  }
  static getStubConfig() {
    return { entity: "sensor.ev_control_charging_status" };
  }
}

// Guard against re-definition (FullyKiosk keeps JS context across soft reloads)
if (!customElements.get("evcontrol-card")) {
  customElements.define("evcontrol-card", EVControlCard);
}

if (!customElements.get("evcontrol-card-editor")) {
  customElements.define("evcontrol-card-editor",
    class extends HTMLElement {
      setConfig(c) { this._c = c; }
    }
  );
}

window.customCards = window.customCards || [];
if (!window.customCards.some(c => c.type === "evcontrol-card")) {
  window.customCards.push({
    type: "evcontrol-card",
    name: "EV Control Card",
    description: "Compact EV charging control panel",
  });
}
