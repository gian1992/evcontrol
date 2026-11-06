# EV Control — Home Assistant Custom Integration

***English** · [Italiano](README.it.md)*

Solar-first EV charging for Home Assistant. It decides, every few seconds, how many
amps the car should draw — from the panels, from the house battery, from the grid, or
not at all — and it explains the decision in plain language instead of leaving you to
reverse-engineer it from a number.

> The configuration UI, the code, this README and the service API are in English,
> with an Italian translation shipped alongside. An Italian translation of this page
> is at [README.it.md](README.it.md).

## What it does

Most solar charging setups answer one question: *is there surplus right now?* That is
enough until the car has to be ready by a certain time, and then surplus-only charging
quietly misses the deadline.

EV Control plans backwards from a deadline instead. You say when the car has to be
full; it works out the latest moment at which falling back to the grid still gets you
there, and until that moment it takes only what the sun gives.

### Charging modes

| Mode | Behaviour |
|---|---|
| `eco` | Solar surplus only. Never imports to charge. |
| `normal` | A guaranteed base current, plus whatever surplus is available on top. |
| `power` | Everything available: surplus, house battery above its floor, then grid up to the contract limit. |
| `auto` | Deadline-driven. Charges on surplus, and falls back to the grid only late enough to still finish in time. |
| `auto-eco` | Like `auto`, but more patient: it gives the sun longer before conceding to the grid. |

### The solar planner

`eco`, `auto` and `auto-eco` are run by the planner in `smart_charging.py`, which is
more careful than a surplus threshold:

- **Anti-cycling.** Once charging has started it holds the charger minimum for as long
  as the panels produce anything, rather than stopping and restarting on every cloud.
  Cars dislike that, and some refuse to restart for minutes afterwards.
- **Adaptive battery reservation.** The power held back for the house battery is
  derived from rolling house-load and PV averages, not from a fixed watt setting.
- **Forecast-aware.** With [Solcast](https://github.com/BJReplay/ha-solcast-solar)
  configured, the production forecast is calibrated against what the array is actually
  doing, so a cloudy overhead does not scale the whole day's plan down.
- **A stop budget**, so a marginal day cannot turn into dozens of start/stop cycles.

### Weekly deadlines

Each weekday can carry one deadline: the moment the car has to be ready by, and how
full it has to be by then. Days without one simply charge on surplus, with nothing to
catch up to.

The percentage defaults to 100% and is what the planner works backwards from. Asking
for 80% needs materially less energy — and skips the CC/CV taper overhead that only
applies near a full charge — so the latest safe moment to fall back to the grid moves
hours later, and the sun gets a longer run at the job. Once the car reaches the
target the deadline is satisfied: it stops counting as urgent, and surplus charging
carries on past it for free rather than stopping there.

The schedule is editable three ways, all writing the same values:

- the integration options (*Settings → Devices & Services → EV Control → Configure →
  Orari di ricarica*)
- the `evcontrol.set_schedule` / `evcontrol.clear_schedule` services
- the Oikos dashboard card, which has a popup for it (see below)

## Installation

### HACS

Add this repository as a custom repository of type *Integration*, install **EV
Control**, then restart Home Assistant.

### Manual

Copy `custom_components/evcontrol/` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

Then: *Settings → Devices & Services → Add Integration → EV Control*.

> Restart, not reload. Home Assistant caches imported Python modules, so reloading the
> config entry will not pick up changed files.

## Configuration

The setup wizard and the options dialog are built from the same sections, so nothing
you can set at install time is missing later:

| Section | What it covers |
|---|---|
| Fotovoltaico | PV production and grid voltage sensors |
| Consumo casa | House load, and whether it already includes the EV |
| Batteria di casa | Home battery SOC, floor, and drain limit |
| Colonnina | Charger current entity, start/stop buttons, min/max current |
| Veicolo elettrico | EV SOC, battery capacity, charging power |
| Orari di ricarica | The weekly deadline, one time per weekday |
| Modalità AUTO | Base current and the phase-1 SOC threshold |
| Ricarica intelligente | The solar planner: thresholds, holds, battery floor |
| Previsione solare (Solcast) | Optional forecast entities |
| Rete e aggiornamento | Contract limit, voltage, refresh rate |

The options dialog is a menu: you step into a section, submit, and land back on the
menu, with edits held until you choose to save them.

## Entities

### Sensors

| Entity | What it tells you |
|---|---|
| `sensor.ev_control_charging_status` | **The one to put on a dashboard.** State is what it is doing; attributes carry why, the plan, and the full raw data the cards read. |
| `sensor.ev_control_charging_outlook` | Will the car reach its target in time, and the latest grid fallback moment. |
| `sensor.ev_control_surplus_power` | Solar surplus (W) |
| `sensor.ev_control_available_charging_current` | The current being asked of the charger (A) |

Plus diagnostic sensors for PV power, house consumption, EV power, both SOCs, the
planner state, smart headroom, battery reserve, grid exchange, stop count, the next
target, the grid-fallback time, and the full/remaining charge durations.

### Controls

- `select.ev_control_charging_mode` — Eco / Normal / Power / Auto / Auto-Eco
- `switch.ev_control_charging_enabled` — charging on/off
- `button.ev_control_start_charging`, `button.ev_control_stop_charging`
- Numbers: grid power limit, normal-mode base current and battery reservation, AUTO
  phase-1 SOC threshold, smart battery floor, solar stop threshold, home battery min SOC

## Services

### `evcontrol.set_schedule`

Set the deadline for one or more days. Persists to the config entry and applies without
reloading the integration, so the planner session survives the edit.

```yaml
action: evcontrol.set_schedule
data:
  day: weekdays        # a weekday, a list, "today", or "all" / "weekdays" / "weekend"
  time: "07:30"
  soc: 80              # optional; omitted, the day keeps the percentage it had
```

### `evcontrol.set_target_soc`

Change how full the car has to be, without touching the times. Same `day` values as
`set_schedule`.

```yaml
action: evcontrol.set_target_soc
data:
  day: saturday
  soc: 80
```

### `evcontrol.clear_schedule`

```yaml
action: evcontrol.clear_schedule
data:
  day: saturday
```

The day's target percentage is left in place: clearing a deadline is usually
temporary, and having the percentage come back with it beats finding it reset.

### Others

| Service | Fields |
|---|---|
| `evcontrol.set_mode` | `mode`: eco, normal, power, auto, auto-eco |
| `evcontrol.start_charging` | — |
| `evcontrol.stop_charging` | — |
| `evcontrol.set_speed` | `speed`: base current in A for Normal mode |

`evcontrol.set_end_time` is a deprecated alias of `set_schedule`, kept so existing
automations keep working. `evcontrol.set_start_time` is inert: there is no configurable
start time, because in AUTO the planner derives it from the deadline, the charge still
needed and the forecast.

## Dashboard cards

Two options, both reading `sensor.ev_control_charging_status`:

- **Oikos card** — the maintained one. Mode tabs, charge and smart toggles, status with
  its reason, the plan row, live metrics, and a popup to create, change and delete the
  weekly deadlines without opening the integration options.
- **Bundled Lovelace card** — `custom_components/evcontrol/www/evcontrol-card.js`, which
  the integration registers automatically at `/evcontrol/evcontrol-card.js`. Works in
  FullyKiosk and Android WebView 68+.

`examples/` holds dashboard YAML and a superseded standalone version of the Lovelace
card, kept for reference.

## Debugging

```yaml
logger:
  logs:
    custom_components.evcontrol: debug
```

The `dettaglio_tecnico` attribute on `sensor.ev_control_charging_status` carries the
planner's own reasoning for the current cycle.

## License

MIT — see [LICENSE](LICENSE).
