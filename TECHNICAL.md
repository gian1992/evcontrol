# EV Control - Technical Documentation

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                   Home Assistant                         │
└─────────────────────────────────────────────────────────┘
              ↑                                    ↓
         Sensors                            State Updates
              ↑                                    ↓
┌─────────────────────────────────────────────────────────┐
│                    EVControlCoordinator                  │
│  (coordinator.py - Updates every 20 seconds)            │
└─────────────────────────────────────────────────────────┘
              ↑              ↓              ↓
        Read sensors    Call helpers   Update data
              ↑              ↓              ↓
        ┌─────────────────────────────────────┐
        │   Platform Entities                 │
        ├─────────────────────────────────────┤
        │ ✓ Sensors (sensor.py)               │
        │   - Surplus, Battery, Grid, Current │
        │   - Debug, PV, Consumption, SOC     │
        │                                     │
        │ ✓ Numbers (number.py)               │
        │   - Grid Power Limit Slider         │
        │   - Normal Mode Current Slider      │
        │                                     │
        │ ✓ Switches (switch.py)              │
        │   - Charging On/Off                 │
        │   - Eco/Normal/Power Mode           │
        │                                     │
        │ ✓ Buttons (button.py)               │
        │   - Start/Stop Charging             │
        └─────────────────────────────────────┘
              ↑              ↑              ↑
        ┌─────────────────────────────────────┐
        │      Helper Functions (helpers.py)  │
        ├─────────────────────────────────────┤
        │ • convert_power_to_unit()           │
        │ • power_to_current()                │
        │ • current_to_power()                │
        │ • calculate_surplus()               │
        │ • get_available_battery_power()     │
        │ • calculate_available_current()     │
        │ • is_charger_connected()            │
        └─────────────────────────────────────┘
```

## File Structure

```
custom_components/evcontrol/
├── __init__.py              # Component initialization
├── manifest.json            # Component metadata
├── py.typed                 # Type hints marker
├── config_flow.py           # UI configuration wizard (5 steps)
├── coordinator.py           # Main data coordinator & update loop
├── helpers.py               # All calculation logic
├── sensor.py                # 10 sensor entities
├── number.py                # 2 number sliders
├── switch.py                # 4 switch entities
├── button.py                # 2 button entities
└── strings.json             # UI text strings
```

## Configuration Flow

The wizard (`config_flow.py`) guides users through:

1. **async_step_user()** → Start
2. **async_step_inverter()** → PV + Voltage sensors
3. **async_step_consumption()** → House consumption setup
4. **async_step_battery()** → Battery parameters
5. **async_step_charger()** → Charger setup
6. **async_step_control()** → Grid limit & refresh rate

Data is collected and merged into `entry.data` on final step.

## Data Flow (Every 20 seconds)

```
1. EVControlCoordinator._async_update_data()
   ↓
2. Get entity states from Home Assistant
   - PV power (W or kW)
   - House consumption (W or kW)
   - Battery SOC (%)
   - Voltage (V)
   - Optional: sold/import power
   ↓
3. Call helpers to calculate:
   - Surplus = PV - (Consumption - EV)
   - Battery Power = drain current × voltage (if SOC > min)
   - Available Current = based on mode + surplus + battery + grid
   ↓
4. Update self.data dictionary
   ↓
5. Notify all entity listeners to update UI
```

## Core Calculation Functions

### `calculate_surplus(pv_power, house_consumption, ev_power, consumption_includes_ev)`
Returns: `surplus_watts`

**Logic:**
```python
if consumption_includes_ev and ev_power:
    actual_consumption = house_consumption - ev_power
else:
    actual_consumption = house_consumption

surplus = pv_power - actual_consumption
```

### `get_available_battery_power(battery_soc, battery_min_soc, battery_max_drain_power)`
Returns: `available_watts`

**Logic:**
```python
if battery_soc <= battery_min_soc:
    return 0
else:
    return battery_max_drain_power
```

### `calculate_available_current(surplus, battery_power, grid_max_power, battery_soc, battery_start_charge_soc, voltage, charger_min_current, charger_max_current, mode)`
Returns: `(available_amps, debug_message)`

**Eco Mode:**
```python
if surplus >= charger_min_current * voltage:
    amps = floor(surplus / voltage)
    amps = min(amps, charger_max_current)
else:
    amps = 0
```

**Normal Mode:**
```python
surplus_amps = floor(max(0, surplus) / voltage)
amps = charger_min_current + surplus_amps
amps = min(amps, charger_max_current)
```

**Power Mode:**
```python
available_power = 0

if battery_soc >= battery_start_charge_soc:
    available_power += battery_power

available_power += max(0, surplus)

remaining_grid = grid_max_power - (available_power / 1000)
if remaining_grid > 0:
    available_power += remaining_grid * 1000

amps = floor(available_power / voltage)
amps = min(amps, charger_max_current)
```

## Coordinator Methods

### `_get_sensor_value(entity_id, default)`
- Fetches state from Home Assistant
- Automatically converts kW/MW to W
- Returns default on error

### `set_mode(mode)`
- Updates `self.data["mode"]` to "eco", "normal", or "power"
- Logged for debugging

### `set_base_current(current)`
- Updates minimum current for Normal mode
- Used by number.py slider

### `set_grid_power_limit(power_kw)`
- Updates grid power limit
- Used by number.py slider

### `set_charging_enabled(enabled)`
- Disables/enables all charging
- Used by on/off switch
- Calls `stop_charging()` when disabled

### `async_start_charging()`
- Presses charger start button entity
- Called from button.py

### `async_stop_charging()`
- Presses charger stop button entity
- Called from button.py or charging disable

## Entity Creation Pattern

All platforms follow this pattern:

```python
async def async_setup_entry(hass, config_entry, async_add_entities):
    # Get or create coordinator
    coordinator = hass.data["evcontrol"].get(config_entry.entry_id)
    if not coordinator:
        coordinator = EVControlCoordinator(hass, config_entry)
        await coordinator.async_config_entry_first_refresh()
        hass.data["evcontrol"][config_entry.entry_id] = coordinator
    
    # Create entities with coordinator
    entities = [Entity1(...), Entity2(...), ...]
    async_add_entities(entities)
```

## Sensor Entities

| Entity ID | Name | Unit | Min/Max | Category |
|-----------|------|------|---------|----------|
| `surplus` | Surplus Power | W | Variable | Measurement |
| `battery_power` | Available Battery Power | W | 0-∞ | Measurement |
| `grid_power` | Grid Power Limit | kW | 0-12 | Config |
| `voltage` | Current Voltage | V | 200-260 | Measurement |
| `available_current` | Available Charging Current | A | 0-32 | Measurement |
| `pv_power` | PV Power Generation | W | 0-∞ | Diagnostic |
| `house_consumption` | House Power Consumption | W | 0-∞ | Diagnostic |
| `ev_power` | EV Power Consumption | W | 0-∞ | Diagnostic |
| `battery_soc` | Battery State of Charge | % | 0-100 | Diagnostic |
| `debug` | Debug Info | str | Variable | Diagnostic |

## Number Entities

| Entity ID | Name | Range | Step | Unit |
|-----------|------|-------|------|------|
| `grid_power` | Grid Power Limit | 3.0-12.0 | 0.5 | kW |
| `normal_current` | Normal Mode Base Current | 6.0-32.0 | 1.0 | A |

## Switch Entities

| Entity ID | Name | Type | Mutually Exclusive |
|-----------|------|------|-------------------|
| `charging_enabled` | Charging Enabled | Boolean | No |
| `mode_eco` | Eco Mode | Mode | Eco/Normal/Power |
| `mode_normal` | Normal Mode | Mode | Eco/Normal/Power |
| `mode_power` | Power Mode | Mode | Eco/Normal/Power |

## Button Entities

| Entity ID | Name | Device Class |
|-----------|------|--------------|
| `start_charging` | Start Charging | RESTART |
| `stop_charging` | Stop Charging | STOP |

## Unit Conversions

| From | To | Multiply |
|------|-----|----------|
| W | kW | ÷ 1000 |
| kW | W | × 1000 |
| MW | W | × 1,000,000 |
| A | W | × Voltage |
| W | A | ÷ Voltage |

**Always assume single-phase 230V unless voltage_entity reports differently**

## Debugging

Enable debug logging:
```yaml
logger:
  logs:
    custom_components.evcontrol: debug
```

**Debug output includes:**
- Surplus calculation breakdown
- Battery availability checks
- Available current calculation per mode
- Charger control commands
- Entity state retrievals

**Debug sensor** (`sensor.evcontrol_debug_info`) shows:
- Current calculation reason
- Mode-specific information
- Why charging is/isn't happening

## Error Handling

- Missing entities: Logged as warnings, defaults used
- Invalid state values: Try-except block, default returned
- Configuration errors: Caught in config_flow
- Update failures: Logged, coordinator retries after interval

## Future Enhancement Points

1. **3-Phase Support**: Modify voltage handling for 3-phase (400V)
2. **Multi-Charger**: Support multiple EV chargers with load balancing
3. **Predictions**: Add ML for surplus forecasting
4. **Logging**: Store historical data in database
5. **Integration**: Link with dynamic pricing/demand response APIs
6. **Custom Card**: Lovelace UI card for better visualization

## Testing Checklist

- [ ] All 5 config wizard steps complete
- [ ] Entities created: 10 sensors, 2 numbers, 4 switches, 2 buttons
- [ ] Surplus calculation correct
- [ ] Battery power calculated properly
- [ ] Each mode produces expected current
- [ ] Sliders update coordinator data
- [ ] Switches toggle modes correctly
- [ ] Buttons send start/stop commands
- [ ] Debug sensor shows calculations
- [ ] Missing entities handled gracefully
- [ ] Coordinator updates every 20 seconds
- [ ] Voltage changes update current calculations
- [ ] Grid limit respected in all modes

## Common Patterns

**Accessing coordinator in platform:**
```python
coordinator = hass.data["evcontrol"][config_entry.entry_id]
```

**Reading a value:**
```python
value = coordinator.data.get("surplus", 0)
```

**Updating a value:**
```python
coordinator.set_mode("eco")
self.async_write_ha_state()
```

**Listening to updates:**
```python
self.async_on_remove(
    coordinator.async_add_listener(self.async_write_ha_state)
)
```
