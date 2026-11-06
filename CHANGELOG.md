# EV Control - Changelog

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
