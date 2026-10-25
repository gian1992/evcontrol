# EV Control - Quick Start Guide

## What You Have

Your new **EV Control** component manages EV charging intelligently based on:
- ☀️ Solar surplus power
- 🔋 Battery availability
- ⚡ Grid power limits
- 🔌 Real-time voltage

## Setup Wizard Overview

The configuration is done through **5 steps**:

### 1️⃣ Solar Inverter Setup
Select sensors for:
- **PV Power**: How much your panels generate (sensor.xxx_power)
- **Voltage**: Current grid voltage (sensor.xxx_voltage)

### 2️⃣ House Consumption
Set up your battery:
- **Does consumption include EV?** Yes → specify EV sensor separately
- **Does consumption exclude EV?** No → leave EV sensor empty

### 3️⃣ Battery Configuration
Set up your battery:
- **Max Drain Current**: Maximum safe current (e.g., 20A)
- **Min SOC**: Stop draining below this (e.g., 20%)
- **Battery SOC Sensor**: entity showing charge level
- **Start Charge SOC**: Use battery from this % (e.g., 70%)

### 4️⃣ EV Charger Setup
Define your charger:
- **Max Current**: Charger limit (e.g., 32A)
- **Min Current**: Minimum to start (e.g., 6A)
- **Start/Stop Buttons**: Control buttons for charger

### 5️⃣ Control Parameters
Final settings:
- **Grid Limit**: Max power from grid for EV (3-12 kW)
- **Refresh Rate**: How often to recalculate (usually 20s)
- **Redundancy Sensors** (optional): Backup surplus calculation

## Three Charging Modes

After setup, you'll have 3 mode switches:

### 🌱 Eco Mode
- Only solar surplus powers the EV
- Best for sunny days, max self-consumption
- Won't charge if cloudy

### ⚙️ Normal Mode
- Guaranteed minimum current (6A default)
- Automatically boosts when surplus available
- Perfect for reliable daily charging

### ⚡ Power Mode
- Maximum power available
- Uses: Battery + Solar + Grid (up to limit)
- Best for fast charging, evening charges

## Real-Time Sensors (Auto-Created)

These show up automatically:
- **Surplus Power** (W) - Solar power after home use
- **Battery Power** (W) - Available from battery right now
- **Available Current** (A) - What the EV can charge at
- **Grid Power Limit** (kW) - Your grid budget
- **Voltage** (V) - Current grid voltage

## Controls (Auto-Created)

### Sliders 🎚️
- **Grid Power Limit**: 3-12 kW (your grid budget)
- **Normal Mode Current**: 6-32A (guaranteed minimum for Normal mode)

### Switches 🔘
- **Charging Enabled**: On/Off for all charging
- **Eco/Normal/Power Mode**: Pick your charging strategy

### Buttons 🔲
- **Start Charging**: Manual start
- **Stop Charging**: Manual stop

## Example Usage

**Morning (Sunny)**
1. EV connected (auto-starts via automation)
2. Switch to **Eco Mode**
3. Charges from solar surplus while house runs

**Afternoon (Clouds)**
1. Switch to **Normal Mode**
2. Gets 6A guaranteed + any surplus
3. Won't drain battery

**Evening (No Sun)**
1. Switch to **Power Mode**
2. Uses battery (if SOC >70%) + grid (up to 12kW limit)
3. Fast charges ready for next day

**Adjust anytime**
- Slide "Normal Mode Current" from 6A → 16A for faster charge
- Slide "Grid Power Limit" from 12kW → 8kW to save grid capacity

## Key Features

✅ **Automatic** - Recalculates every 20 seconds  
✅ **Safe** - Respects battery SOC, charger limits, grid power  
✅ **Smart** - Uses voltage sensor for accurate current  
✅ **Flexible** - 3 modes for different scenarios  
✅ **Visible** - Real-time sensors for monitoring  
✅ **Reliable** - Fallback sensors for redundancy  

## Troubleshooting

**Sensors not updating?**
- Check that all entity IDs exist in Developer Tools → States
- Restart Home Assistant
- Check logs for "evcontrol" errors

**Current always 0?**
- Check "Charging Enabled" switch is ON
- Verify "Min Current to Start" (default 6A) - need at least this much power
- In Eco mode, need solar surplus; try Normal mode first

**Wrong calculations?**
- Check "Debug Info" sensor for calculation breakdown
- Verify consumption sensor includes/excludes EV correctly
- Look at grid voltage sensor - should be ~230V

## What's Next?

1. ✅ Install component (already done)
2. ✅ Run setup wizard (just answer questions)
3. → Create automations to auto-start/stop
4. → Set up a Lovelace dashboard (see example_configuration.yaml)
5. → Monitor and adjust grid power limit as needed

## Support

See **README.md** for:
- Full entity reference
- Calculation formulas
- Automation examples
- Advanced configuration

Enjoy smart EV charging! ⚡🌞
