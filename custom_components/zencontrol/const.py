"""Constants for the zencontrol integration."""
DOMAIN = "zencontrol"

# Branding
INTEGRATION_AUTHOR = "Lumen Resources"
INTEGRATION_AUTHOR_URL = "https://www.lumenresources.com.au"
HARDWARE_MANUFACTURER = "zencontrol"

# Config / options keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_EVENT_PORT = "event_port"
CONF_USE_MULTICAST = "use_multicast"
CONF_SCENES = "scenes"                     # list[dict] — manually configured scenes
CONF_SCENE_ADDRESS = "scene_address"       # DALI address for scene (0-79)
CONF_SCENE_NUMBER = "scene_number"         # scene number 0-12
CONF_SCENE_NAME = "scene_name"             # optional display name

# Defaults
DEFAULT_PORT = 5108
DEFAULT_EVENT_PORT = 6970   # Unicast receive port on the HA side
DEFAULT_USE_MULTICAST = False

# How often (seconds) to ping the controller and re-assert event config
PING_INTERVAL = 30

# Unique-ID prefixes
UID_GROUP = "group"
UID_SHORT = "short"
UID_SCENE = "scene"
UID_PROFILE = "profile"
UID_OCCUPANCY = "occupancy"
UID_BUTTON = "button"
UID_ABSOLUTE = "absolute"
UID_BUTTON_LED = "button_led"

# HA data keys (stored in hass.data[DOMAIN])
DATA_COORDINATOR = "coordinator"
DATA_EVENT_LISTENER = "event_listener"

# Button event types (exposed on the HA `event` entity and device triggers)
BUTTON_EVENT_PRESS = "press"
BUTTON_EVENT_HOLD = "hold"

# Dispatcher signal fired for each button press/hold.
# Format args: entry_id.  Payload: (cd_address, instance_number, event_type).
SIGNAL_BUTTON_EVENT = "zencontrol_button_event_{}"


def get_entry_config(entry) -> dict:
    """Merge config entry data and options, with options taking precedence.

    The initial config flow stores values in entry.data.  The options flow
    stores updates in entry.options (HA does not allow mutating entry.data
    after setup).  This helper merges both so callers always see the latest
    values regardless of which store they live in.
    """
    return {**entry.data, **entry.options}
