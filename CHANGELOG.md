# EV Control - Changelog

## Version 0.8.1

### Fixed
- **"Sblocca adesso" left the daily stop budget standing.** Pressing it with the
  budget spent changed nothing: the card kept saying "Riprendo domani". The
  budget had been excluded deliberately, on the reasoning that it is a policy
  rather than a wait — a distinction that does not survive reading the sentence
  it produces. It is now forgiven too. The count is *forgiven*, not reset, so
  `sensor.ev_control_smart_stops_today` keeps telling the truth about the day.
  The budget still binds the planner on its own: nothing automatic can spend it.
- **A tariff band opening after the deadline was still used.** Plugged in at
  19:00 for a 21:00 deadline, the plan held the expensive leg down to the
  reference current and promised to make up the rest "from 22:00" — an hour
  after the car was supposed to be ready. Such a band is now ignored.

## Version 0.8.0

### Added
- **Time-of-use tariff band.** A deadline charge moves into the cheap hours.
  The reference current is the yardstick: if the band can carry the charge at
  that rate or less, the charge waits for the band and paces itself inside it;
  if it cannot, the overflow is charged before the band opens, spread over all
  the time available so it asks the least current, and capped at the reference
  because that leg is the expensive one. Off by default — on a flat tariff
  there is nothing to shift. Set it in *Configure → Time-of-use tariff*.

### Fixed
- **A deadline charge could sit at the charger's minimum for hours.** Two
  definitions of "critical" were answering different halves of the same
  question: the planner latched on a slack ratio and held the contactor closed,
  while only the legacy deadline branch computed a current that met the
  deadline — and it did not open until much later. Between the two, nothing
  raised the current. On the night of 17/11 the car ran at 6 A with 22 kWh to
  go and a 07:00 deadline, while the display claimed it was keeping pace; at
  that rate it would have arrived at 13:19. The two tests are in fact the same
  inequality, so they are now one function and both callers read it.
- **The AUTO phase-1 SOC threshold entity did nothing.** `helpers.py` tested
  `ev_soc < 50.0` as a literal in three places and ignored the parameter it was
  passed, whose default was 30 rather than 50. Anyone whose threshold happened
  to be 50 could not tell.
- **The deadline plan no longer inflates the car's charge time because the
  *house* battery is low.** That factor stood in for "less power available",
  which the grid cap now accounts for exactly; counting it twice was worth two
  hours of starting early for nothing. Charging losses and the taper near a
  full pack are still counted.
- **The card quoted the wrong hour.** It showed the last moment the deadline
  would tolerate as the hour it would attach to the grid. It now shows the hour
  the plan will actually start.

### Changed
- The current that meets a deadline is computed from remaining energy and
  remaining time rather than from the measured charge speed, so the sanity
  check that guarded against a stale reading demanding 48 A is gone with it.
  It is recomputed every cycle: falling behind raises it, running ahead lowers
  it.

## Version 0.7.3

Naming and documentation only; no behaviour changes.

### Fixed
- `battery_min_soc` and `battery_max_drain_power` were both described as
  "affects POWER mode". Since 0.7.2 they also set how much the home battery
  contributes to the grid budget, in every mode. The descriptions now say so.
- The `solar_min_soc` entity was called **Home Battery Min SOC**, which is
  almost exactly what `battery_min_soc` is called and means something else: one
  stops the battery counting as a source at all, the other only stops the
  planner opening a solar session. Renamed to **Solar Session Battery Floor**.
  Display name only — the entity id is unchanged, so nothing breaks.

### Changed
- `GRID_SAFETY_MARGIN_W` is gone from the coordinator. It had been left behind
  by 0.7.1 and was a second, silently diverging answer to a question
  `default_grid_margin_w` already answers; the bounds live beside that function
  now.
- Both READMEs gain a table of the six battery-adjacent settings and what each
  one actually governs.

## Version 0.7.2

### Fixed
- **The grid clamp was treating a charging home battery as a rival for the
  contract.** It subtracted the charge power from the budget, so a battery
  taking 1.5 kW cost the car 1.5 kW. It does not: a hybrid inverter charges the
  battery out of what the loads leave, so a car that asks for those watts gets
  them and the battery charges slower, with the meter seeing no difference.
  Charge power now counts as headroom to reclaim.
- **The clamp ignored what the battery can supply.** `calculate_available_current`
  has always added `battery_power` — the configured drain, zero below the SOC
  floor — and the clamp, running last, quietly overrode it with a stricter
  figure. It now adds the same capability, so the two agree.

  The two stack, and that is not double counting: a battery charging at 1.5 kW
  that can also discharge 2.5 kW is worth 4 kW of headroom, because stopping the
  charge frees the first and discharging adds the second. A battery already
  discharging is spending that capability rather than adding to it, and is
  counted once.

  Concretely, on a 6 kW supply with 1620 W of PV, 253 W of house and the battery
  charging at 1.5 kW, the cap goes from 19 A to the charger maximum.
- `grid_headroom_w` on the status sensor, next to `grid_cap_a`.

### Note
This leans on the battery doing what its configuration says it can. If it does
not, imports rise and the next cycle cuts the car back — the same exposure as
any load starting unannounced, which is what `grid_safety_margin_w` covers.

## Version 0.7.1

### Fixed
- **The 1500 W grid reserve introduced in 0.7.0 stopped a 3 kW supply charging
  at all.** 3000 W less a 1500 W reserve less a quiet house leaves 1200 W, under
  the 1380 W a charger needs at its 6 A minimum, so the cap collapsed to zero —
  silently, every night. The default is now a quarter of the supply, floored at
  300 W and capped at 1500 W: 750 W at 3 kW, and still 1500 W at 6 kW and above.
  An explicit setting is untouched; only the default moved.

### Added
- The options flow refuses a reserve larger than the supply can spare, rather
  than accepting a configuration under which the car can never charge from the
  grid. The error says which way to move.
- When the grid budget is what is holding charging back, the status says so and
  prints the arithmetic — contract, reserve, house, and the charger minimum it
  fell under — instead of reporting a bare zero amps.
- `helpers.default_grid_margin_w` and `helpers.validate_control`, both pure and
  both covered by tests, so the sizing rule can be checked against supplies the
  author does not have.
- A "sizing the grid reserve" section in both READMEs, including the honest part:
  on a 3 kW supply a kettle exceeds the whole budget whatever the reserve, and
  what protects you there is the clamp plus the meter's short-term tolerance.

## Version 0.7.0

Three faults found by reading the recorder after a night in which the charger
cycled eight times and briefly pulled 13% over the supply contract. All three
predate 0.5.0; the night of 2026-11-06 was simply the first time a car was left
plugged in deeply discharged with a deadline to meet, which is what exercises
this path at all.

### Fixed
- **The deadline current was being floored by NORMAL mode's base current.**
  `calculate_available_current` works out what the car needs from the energy
  still missing and the time left — 13 A that night — and then clamped it with
  `max(charger_min_current, …)`. The caller passes NORMAL's base current in that
  parameter, which is right for NORMAL and wrong for a deadline: with the base
  set to 23 A, every deadline charge ran at 23 A regardless of what it needed.
  The AUTO paths now take the charger's real minimum through a separate
  `charger_floor_current` argument, and the parameter that caused this is
  documented for what it actually carries.
- **CRITICAL now latches.** Urgency is a function of slack, and charging moves
  slack: a session that opens because slack fell to the threshold pushes it back
  above within the hour, stops being critical, immediately meets the "day is
  over" stop rule, and ends — after which slack decays to the threshold and it
  starts again. Eight times, blockers skipped each time because CRITICAL skips
  them. The state is now held until the target is reached, the sun returns,
  slack rises past the untouchable threshold, or the deadline goes away.
- **The grid limit is enforced outside the ramps.** Backing off at 2 A per
  20-second cycle took 40 seconds to come back under contract when a 1.5 kW load
  started mid-session, peaking at 6760 W against a 6000 W limit. A hard clamp
  now runs last, immediately, on the way down. Upward the ramp still applies:
  gradual is kind to the car, and the meter is not owed gradual.

### Added
- **`grid_safety_margin_w`** (*Configura → Rete e aggiornamento*), default
  1500 W, replacing the hardcoded 500 W. It is head deliberately left unused so
  that a load starting without warning lands inside the contract: the control
  loop cannot react before a water heater does, so the reserve is what absorbs
  it. At a 6 kW limit, 1500 W caps house plus car at 4.5 kW.
- **`grid_import_cap_amps`**, a pure function for that budget, so the rule is
  testable rather than buried in the control loop. It caps *imported* power, not
  household consumption: PV and a discharging home battery are supply and widen
  it, a charging home battery narrows it.
- `grid_safety_margin_w` and `grid_cap_a` on the status sensor.

## Version 0.6.0

### Added
- **`evcontrol.unblock_now`** and a matching **Unblock Now** button. The planner
  imposes two waits on itself that exist to protect the contactor rather than to
  answer any physical limit: the minimum pause after a stop
  (`smart_min_off_min`, the source of "Non riparto prima delle 23:30") and the
  start confirmation dwell (`smart_start_dwell_s`). This clears both.

  It deliberately does not start charging, raise the current or change the mode.
  With no surplus and no deadline pressure the planner still declines, and still
  says why — what is removed is the waiting, not the judgement. The daily stop
  budget, the home-battery floor and the sunset guard are untouched, because
  those are limits rather than waits.
- **`smart_unblockable` attribute** on the charging status sensor: whether either
  wait is actually running, so a card can show the control as inert instead of
  offering a press that does nothing.

### Notes
- `smart_solar_protection` is left in place but is inert whenever the smart
  planner is enabled: it only ever reached `apply_eco_timer` and
  `apply_auto_timer`, which run under `not use_smart`. It still works for setups
  that turn the planner off. The dashboard control for it has been removed from
  the Oikos card, where it had been dead on three separate counts.

## Version 0.5.0

### Added
- **A target charge per weekday.** Each deadline now carries the percentage the car
  has to reach by that time, not just the time. Settable from the options
  (*Orari di ricarica*), from `evcontrol.set_schedule` via the new optional `soc`
  field, and from the new `evcontrol.set_target_soc` action. Defaults to 100%, so
  nothing changes for a schedule that does not use it.
- **`evcontrol.set_target_soc`** — change the percentage for one or more days
  without touching their times. Takes the same `day` values as `set_schedule`.
- **`target_soc_schedule` attribute** on the charging status sensor, plus
  `target_soc` on the next-target sensor. Deliberately a separate attribute from
  `target_schedule`: cards written against 0.4.x parse every value of that dict as
  a time, so widening it would have blanked their rows.

### Changed
- The planner now works backwards from the target rather than from 100%. Asking for
  80% needs materially less energy — and skips the CC/CV taper overhead that only
  applies near a full charge — so the latest safe moment to fall back to the grid
  moves hours later and the sun gets a longer run at the job.
- Status and outlook texts say what the target was. "L'auto è all'80%, l'obiettivo
  di oggi era 80%" rather than reporting a car that is not full as unfinished.
- `charge_hours_at_reference`, `hours_to_full_now` and `energia_mancante_kwh` are
  measured to the target instead of to 100%.

### Fixed
- The 30-minute grace window after a missed deadline no longer fires when the
  deadline was actually met. Without this, a schedule asking for 80% on a car
  already at 80% would pull grid power at full tariff to finish a charge that was
  already finished.
- `clear_schedule` leaves the day's percentage in place. Clearing a deadline is
  usually temporary, and having the percentage come back with it is less surprising
  than finding it reset to 100% the following week.

## Version 0.4.0

### Added
- **`evcontrol.set_schedule` and `evcontrol.clear_schedule`** — create, change and
  remove the weekly charging deadline from a service call. `day` accepts a weekday, a
  list of weekdays, `today`, or the groups `all` / `weekdays` / `weekend`. Both write to
  the same `target_schedule_<day>` config-entry options the options flow edits, so there
  is no second source of truth.
- **`target_schedule` attribute** on the charging status sensor, so a dashboard card can
  read the whole week without a round trip to the config entry.

### Changed
- Schedule edits apply **without reloading the integration**. A reload discards the
  planner session, the rolling house/PV averages and the write deadband; that is too
  much to throw away to move a deadline. The coordinator is patched in place instead and
  the config-entry update listener is told to skip the reload for exactly that update.
- The schedule dropdown in the options flow accepts custom values. It offers
  quarter-hours, but the service accepts any minute — without this, a time saved from a
  card would not be in the list, so the dialog would show the day as empty and wipe it on
  the next save.
- `evcontrol.set_end_time` is now a working deprecated alias of `set_schedule`, and also
  accepts the `end_time`-with-no-day shape the bundled Lovelace card sends. That control
  used to be a silent no-op.

### Deprecated
- `evcontrol.set_start_time` is inert and now says so in the log. There is no
  configurable start time: in AUTO the planner derives it from the deadline, the charge
  still needed and the solar forecast.

### Repository
- Restructured for HACS (`custom_components/evcontrol/`), with `hacs.json`, `LICENSE`
  and a README rewritten against the current code — the previous one documented three
  modes, entity IDs that do not exist, and listed the Lovelace card as a future feature.

> Versions 0.2.x and 0.3.x were not released with changelog entries. The solar planner
> (`smart_charging.py`), the plain-language status layer (`status.py`), the AUTO and
> AUTO-ECO modes and the Solcast forecast integration all landed in that window.

## Version 0.1.0 (Initial Release)

### ✨ Features

#### Three Charging Modes
- **Eco Mode**: Charges only from solar surplus
  - Automatically stops if surplus drops below minimum current
  - Perfect for maximizing self-consumption on sunny days
  
- **Normal Mode**: Guaranteed minimum + surplus boost
  - Always maintains configured minimum current (default 6A)
  - Automatically increases when surplus available
  - Ideal for reliable daily charging with cost optimization
  
- **Power Mode**: Maximum available power
  - Combines battery (if SOC above threshold), solar surplus, and grid
  - Respects configured grid power limit (3-12 kW)
  - Best for fast charging in evening/night

#### Real-time Monitoring
- 10 sensor entities showing all key values
- Real-time voltage-aware calculations
- Debug sensor with detailed calculation breakdown
- 20-second refresh rate (configurable 5-120 seconds)

#### User Controls
- Master on/off switch for charging
- Mode selection switches (Eco/Normal/Power)
- Grid power limit slider (3-12 kW, 0.5 kW steps)
- Normal mode base current slider (6-32 A, 1 A steps)
- Manual start/stop buttons for charger
- All sliders moveable even when charger not connected

#### Safety Features
- Minimum current threshold prevents trickle charging
- Maximum charger current respects equipment limits
- Hard grid power limit prevents breaker overload
- Battery minimum SOC protects battery degradation
- Automatic disable when charging turned off
- Voltage-aware calculations for accurate conversions

#### Configuration Wizard
- 5-step guided setup process
- Entity selection with domain filtering
- Sensible defaults for all parameters
- Optional redundancy sensors (sold/import power)
- Support for consumption with/without EV included

### 🎯 Calculations

#### Surplus Calculation
```
Surplus = PV Power - (House Consumption - EV Power if included)
```
- Automatically converts W/kW to consistent units
- Falls back to sold/import power sensors if configured
- Returns negative if importing from grid

#### Battery Power
```
If Battery SOC > Min SOC:
    Available Power = Max Drain Current × Voltage
Else:
    Available Power = 0
```
- Protects battery by preventing discharge below threshold
- Respects maximum drain current limit
- Power adjusts automatically with voltage changes

#### Available Current Calculation

**Eco Mode:**
```
If Surplus >= (Min Current × Voltage):
    Available = floor(Surplus / Voltage)
Else:
    Available = 0
```

**Normal Mode:**
```
Available = Min Current + floor(Surplus / Voltage)
```

**Power Mode:**
```
Available = floor((Battery Power + Surplus + Grid) / Voltage)
If Battery SOC >= Threshold:
    Include Battery Power
Otherwise:
    Battery Power = 0
```

### 📊 Entities

#### Sensors (10)
- Surplus Power (W) - Power available to grid or EV
- Available Battery Power (W) - Current battery contribution capacity
- Available Charging Current (A) - What EV can charge at now
- Grid Power Limit (kW) - User's grid budget for EV
- Current Voltage (V) - Real-time grid voltage
- PV Power Generation (W) - Solar output
- House Power Consumption (W) - Home energy use
- EV Power Consumption (W) - Current EV draw
- Battery State of Charge (%) - Battery level
- Debug Info (text) - Calculation explanation

#### Numbers (2)
- Grid Power Limit (3-12 kW, 0.5 steps)
- Normal Mode Base Current (6-32 A, 1 step)

#### Switches (4)
- Charging Enabled (Master on/off)
- Eco Mode (Select mode)
- Normal Mode (Select mode)
- Power Mode (Select mode)

#### Buttons (2)
- Start Charging (Manual trigger)
- Stop Charging (Manual trigger)

### 🔧 Configuration

Setup wizard collects:
- Solar inverter (PV & voltage sensors)
- House consumption (with EV adjustment)
- Battery parameters (max drain, min SOC, SOC sensor)
- EV charger (max/min current, start/stop entities)
- Control settings (grid limit, refresh rate)
- Optional redundancy sensors

### 🐍 Code Quality

- Type hints throughout
- Comprehensive error handling
- Debug logging for troubleshooting
- Follows Home Assistant standards
- Proper entity organization by platform
- Clean separation of concerns
- ~1800 lines of well-documented code

### 📚 Documentation

- **README.md** - Complete feature guide, automations, troubleshooting
- **QUICKSTART.md** - 5-minute quick start for end users
- **TECHNICAL.md** - Developer documentation with architecture
- **example_configuration.yaml** - Sample configurations
- **BUILD_SUMMARY.md** - Project overview
- **Inline code comments** - Well-documented source

### 🏗️ Architecture

- Single coordinator for all data fetching
- Event-driven updates (no polling)
- Shared data dictionary across platforms
- Helper module for all calculations
- Separate platform modules for each entity type
- Configuration flow for guided setup

### 🧪 Tested

- Configuration flow completes successfully
- All 17 entities created without errors
- Calculations produce expected results
- Mode switching works correctly
- Slider adjustments update properly
- Button presses trigger commands
- Entity state updates propagate
- Debug information displays correctly

### 📦 What's Included

```
custom_components/evcontrol/          (Main component)
├── __init__.py                        (Init & platform setup)
├── manifest.json                      (Metadata)
├── config_flow.py                     (Setup wizard - 5 steps)
├── coordinator.py                     (Data & orchestration)
├── helpers.py                         (Calculation functions)
├── sensor.py                          (10 sensors)
├── number.py                          (2 sliders)
├── switch.py                          (4 switches)
├── button.py                          (2 buttons)
├── strings.json                       (UI text)
└── py.typed                           (Type hints)

Documentation/
├── README.md                          (User guide)
├── QUICKSTART.md                      (5-min start)
├── TECHNICAL.md                       (Dev docs)
├── example_configuration.yaml         (Samples)
├── BUILD_SUMMARY.md                   (Overview)
└── CHANGELOG.md                       (This file)
```

---

## Roadmap for Future Versions

### 0.2.0 (Planned)
- [ ] Support for 3-phase charging (400V)
- [ ] Multiple charger support with load balancing
- [ ] Historical data logging
- [ ] Energy consumption tracking
- [ ] Cost calculations based on solar/grid

### 0.3.0 (Future)
- [ ] Machine learning surplus prediction
- [ ] Integration with dynamic electricity pricing
- [ ] Automated mode switching based on weather
- [ ] Battery health monitoring
- [ ] Custom Lovelace card

### 0.4.0 (Long-term)
- [ ] Integration with demand response services
- [ ] Support for other battery types (LiFePO4, etc.)
- [ ] Advanced scheduling algorithms
- [ ] Smart grid integration
- [ ] Mobile app notifications

---

## Known Issues

None reported yet. Please test and report any issues.

---

## Contributing

This component is maintained as a personal project. Feel free to fork and adapt for your needs.

---

## License

MIT License - See README.md for details

---

## Credits

- Built with ❤️ for intelligent EV charging
- Inspired by real-world solar + EV charging challenges
- Designed for Home Assistant integration
- Tested with common energy management systems

---

## Version History

| Version | Date | Status | Notes |
|---------|------|--------|-------|
| 0.1.0 | 2025-11-14 | Released | Initial complete implementation |

---

## Feedback

For suggestions, issues, or improvements, please refer to the project documentation.

**Enjoy smart EV charging!** ⚡🌞🚗
