# zencontrol for Home Assistant

A Home Assistant custom integration for **zencontrol** Application Controllers, using the TPI Advanced protocol over UDP/TCP.

Developed and maintained by **[Lumen Resources](https://www.lumenresources.com.au)**.

---

## Requirements

- A zencontrol Application Controller (any model) on your local network
- The **TPI Advanced licence** enabled on the controller (contact your zencontrol installer or Lumen Resources if unsure)
- Home Assistant 2024.1 or later

---

## Installation

### Option A — HACS (recommended)

1. Open HACS in your Home Assistant sidebar.
2. Go to **Integrations → Custom repositories**.
3. Add the URL of this repository and select **Integration** as the category.
4. Search for **zencontrol** and click **Download**.
5. Restart Home Assistant.

### Option B — Manual

1. Download or clone this repository.
2. Copy the `custom_components/zencontrol` folder into your Home Assistant `config/custom_components/` directory.  
   The result should be: `config/custom_components/zencontrol/__init__.py`
3. Restart Home Assistant.

---

## Setup

### Adding a controller

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **zencontrol**.
3. Enter the **IP address** of your zencontrol controller.  
   Leave the port at the default (`5108`) unless your installer changed it.
4. Home Assistant will connect to the controller and display its name for confirmation.
5. Choose your **event transport**:
   - **Unicast** (default, recommended) — the controller sends live state updates directly to Home Assistant. Works across VLANs and in Docker environments.
   - **Multicast** — the controller broadcasts updates to the local network. Only use this if unicast does not work in your network setup.
6. Click **Submit**. Home Assistant will discover all devices and create entities automatically.

### Multiple controllers

Repeat the setup process for each controller. Each controller appears as a separate device in Home Assistant with its own set of entities.

---

## Entities created

After setup, the integration creates the following entities for each controller:

| Entity type | What it represents | Notes |
|---|---|---|
| **Light** (group) | A DALI lighting group | Auto-discovered from the controller |
| **Light** (short address) | An individual DALI fixture | Auto-discovered |
| **Switch** (relay) | A DALI relay output | Auto-discovered; detected by device type |
| **Switch** (button LED) | A keypad push-button indicator LED | Auto-discovered; disabled by default — see below |
| **Binary sensor** (occupancy) | An occupancy / motion sensor | Auto-discovered; see below |
| **Binary sensor** (absolute input) | A DALI absolute input, as on/off | Auto-discovered; see below |
| **Event** | A keypad push button | Auto-discovered; fires `press` / `hold`; see below |
| **Select** | Active controller profile | Shows and controls the current profile |

### Colour control

Colour-capable fixtures are fully supported:

- **Tuneable white (Tc)** — adjust colour temperature in Kelvin
- **RGB / RGBW** — full colour control including a dedicated white channel
- **CIE XY** — precise colour point control

The correct colour mode is detected automatically for each fixture.

### Brightness

All lights support brightness control. Transitions are also supported — set a transition time in seconds when calling the `light.turn_on` service.

### Occupancy sensors

DALI occupancy sensors (motion detectors) are automatically discovered and appear as **binary sensor** entities with device class `occupancy`. When motion is detected the sensor turns `on`; it returns to `off` after the hold time configured on the controller has elapsed with no further motion.

- Each sensor instance on the DALI bus appears as a separate entity.
- All sensors are listed under the controller device — there is no per-room grouping.
- The hold time is read directly from the controller at startup, so no manual configuration is required.
- Initial state is recovered on startup: if the controller reports that motion was detected more recently than the hold time, the sensor will start in the `on` state.
- Each sensor entity exposes three diagnostic attributes: `dali_cd_address` (the raw TPI address, 64–127), `cd_index` (the control device index, 0–63), and `hold_time_s` (the hold time in seconds).

### Push buttons (keypads)

DALI push buttons are automatically discovered and appear as **event** entities. Each physical button becomes one event entity whose state is the timestamp of its most recent event, with an `event_type` attribute of either `press` or `hold`.

- Each button also appears as a **device trigger** in the automation UI (e.g. *"Kitchen Button 1" pressed / held*), so you can build automations without touching YAML.
- Buttons are grouped under the controller device.

### Button LEDs

Keypads with controller-managed indicator LEDs expose a **switch** per button to turn the LED on or off. Because the DALI protocol provides no way to detect whether a given button physically has an LED, these switches are **disabled by default** — enable the ones that correspond to real LEDs on your keypads. State is optimistic (there is no LED-change push event).

### Absolute inputs

DALI absolute inputs are discovered as **binary sensor** entities, treated as an on/off input — `on` when the controller reports a non-zero value, `off` when it reports zero. These are read-only inputs: Home Assistant reflects their state but cannot drive them. The last raw 16-bit value is exposed as the `raw_value` attribute for diagnostics. The sensor has no state until the input first reports.

---

## Options — configuring scenes

Scenes let you trigger a specific DALI scene on a group or individual fixture from Home Assistant automations and dashboards.

1. Go to **Settings → Devices & Services**.
2. Find your zencontrol controller and click **Configure**.
3. Select **Add a scene**.
4. Choose the target type (**Group** or **Short Address**) and enter the target number.
5. Enter the **scene number** (0–12).
6. Optionally enter a display name. If left blank, the name is fetched automatically from the controller.
7. Click **Submit**, then **Save and finish**.

A `scene` entity will appear in Home Assistant. Activating it recalls the scene on the target device.

To remove a scene, return to **Configure** and select **Remove a scene**.

---

## Live state updates

The integration receives live state updates from the controller via push events — no polling is needed. The following events are handled automatically:

- Light level changes (from wall switches, automations, or other sources)
- Colour changes
- Scene activations
- Profile changes
- Occupancy detections

The controller is pinged every 30 seconds to confirm events are still flowing. If the controller has rebooted, event delivery is re-established automatically.

---

## Troubleshooting

### The integration fails to set up

- Confirm the controller's IP address is correct and reachable from the Home Assistant host.
- Confirm the **TPI Advanced licence** is enabled on the controller. The integration will not connect without it.
- Check that port **5108** is not blocked by a firewall between Home Assistant and the controller.

### Entities do not appear

- Wait up to 60 seconds after setup — the integration waits for the controller to finish its startup sequence before discovering devices.
- Check the Home Assistant logs (**Settings → System → Logs**) for any errors from the `zencontrol` integration.

### State updates are delayed or not working

- If using unicast (default), ensure there is no firewall blocking inbound UDP on port **6970** to the Home Assistant host.
- If Home Assistant runs in Docker, ensure the container's UDP port 6970 is mapped to the host.
- Try switching to **Multicast** in the integration options if unicast is not viable in your network.

### Light icons do not update immediately

State is updated optimistically as soon as a command is sent, and confirmed by the controller's push event shortly after. If the icon does not update at all, check that the controller is reachable and events are flowing (see above).

### Occupancy sensors do not appear

- Confirm the sensor is commissioned on the DALI bus and visible in the zencontrol interface.
- Occupancy sensors are discovered as DALI control devices — they must be addressed and configured on the controller before the integration can find them.
- Check the Home Assistant logs for any discovery errors from the `zencontrol` integration.

### Occupancy sensor stays on / does not clear

The sensor clears automatically after the hold time configured on your zencontrol controller. If the sensor never clears, check the controller's sensor settings to confirm a hold time is set. If the hold time is 0, the integration falls back to a default of 60 seconds.

### A button / absolute input / sensor is discovered but never updates

The entity appears (so discovery found the instance) but its state never changes. This is almost always a **controller-side** issue, not the integration:

- The controller only forwards an instance's events over TPI when that instance is **active in the currently running profile**. An instance that isn't used by the active profile stays silent on the TPI feed **even though the controller's own event log shows it firing**. Enable/use the instance in the active profile on the controller.
- Absolute inputs emit only on a **value change** — turning a dial or moving a slider. A momentary press may not change the value and therefore emits nothing.
- Confirm TPI events are reaching Home Assistant at all (e.g. a button on the same controller updates its `event` entity). If nothing from a controller updates, re-check the event transport (unicast/multicast) settings.

---

## Technical details

- Protocol: TPI Advanced over UDP (default) or TCP
- Default command port: 5108
- Default event receive port: 6970 (unicast) / 6969 (multicast)
- DALI addressing: groups 0–15 (address 64–79), short addresses 0–63, control devices 0–63 (TPI address 64–127)
- Colour types: Tc (tuneable white), RGBWAF (up to 6 channels), CIE XY
- Occupancy: individual sensor instances via DALI control device instances; hold timer managed by Home Assistant using the controller's configured hold time

---

## Support

For support, please contact **[Lumen Resources](https://www.lumenresources.com.au)** or open an issue on GitHub.
