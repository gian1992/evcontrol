# EV Control Card

Professional EV charging control dashboard with real-time data visualization and bidirectional control.

## Features

✨ **Beautiful Design**
- Dark gradient background with modern UI
- SVG car visualization with animated battery fill
- Color-coded battery indicator (green/yellow/red)
- Smooth transitions and hover effects

⚡ **Full Control**
- Mode selector (ECO, NORMAL, POWER, AUTO, AUTO-ECO)
- Start/Stop charging buttons
- Real-time power calculations
- Debug information panel

📊 **Real-time Data**
- Available current display
- PV power generation
- Power surplus/deficit
- House consumption
- Battery capacity and voltage
- EV charging status

## Installation

### Option 1: Copy to `/config/www/`

1. Copy the entire `evcontrol-card` folder to your Home Assistant `/config/www/` directory
2. Add the following to your `configuration.yaml`:

```yaml
frontend:
  extra_module_url:
    - /local/evcontrol-card/evcontrol-card.js
```

3. Restart Home Assistant

### Option 2: YAML Card Configuration

Add to your dashboard YAML:

```yaml
type: custom:evcontrol-card
entity: sensor.debug_info
```

## Configuration

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | `custom:evcontrol-card` |
| `entity` | string | Entity ID of the `sensor.debug_info` |

## Requirements

- Home Assistant 2024.1.0 or newer
- `sensor.debug_info` entity with the following attributes:
  - `mode` - Current charging mode
  - `available_current` - Available current in amps
  - `pv_power` - PV generation in watts
  - `surplus` - Power surplus in watts
  - `house_consumption` - House consumption in watts
  - `battery_soc` - Battery state of charge %
  - `ev_soc` - EV battery state of charge %
  - `voltage` - Grid voltage
  - `ev_power` - EV charging power
  - `battery_power` - Battery power
  - `grid_power_limit` - Grid power limit in kW
  - `debug_message` - Status message

## Services

The card calls the following services:

### Set Mode
```
Service: evcontrol.set_mode
Data:
  mode: ECO|NORMAL|POWER|AUTO|AUTO-ECO
  entity_id: sensor.debug_info
```

### Start Charging
```
Service: evcontrol.start_charging
Data:
  entity_id: sensor.debug_info
```

### Stop Charging
```
Service: evcontrol.stop_charging
Data:
  entity_id: sensor.debug_info
```

## Browser Compatibility

- Chrome/Chromium 90+
- Firefox 88+
- Safari 14+
- Edge 90+

## License

MIT

## Support

For issues and feature requests, visit the [GitHub repository](https://github.com/gian1992/evcontrol)
