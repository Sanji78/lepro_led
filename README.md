# Lepro LED (Home Assistant Custom Integration)

Monitor and control your **Lepro LED** devices from Home Assistant.  
This custom integration logs in to **Lepro Cloud**, retrieves your lights and strips, and exposes them as controllable lights in HA.

[![Validate with HACS](https://img.shields.io/badge/HACS-validated-41BDF5)](https://hacs.xyz/)  
[![hassfest](https://img.shields.io/badge/hassfest-passing-brightgreen)](https://developers.home-assistant.io/docs/creating_integration_manifest/)  
[![MIT License](https://img.shields.io/badge/license-MIT-informational)](LICENSE.md)

> ⚠️ This is a third‑party project, not affiliated with Lepro.

---

## ✨ Features

- Login with your **Lepro** account (email + password).  
- Automatically discovers all **Lepro lights and strips** in your account.  
- Sensors for:
  - Connection state and online/offline status
  - Device model, firmware, and MAC address
  - Brightness, color temperature, and RGB values
- Turn lights **on/off**, set **brightness**, **color**, and **effects**.  
- Automatic token renewal to maintain connectivity.

---

## 🔧 Installation

### Option A — HACS (recommended)
1. Make sure you have [HACS](https://hacs.xyz/) installed in Home Assistant.
2. In Home Assistant: **HACS → Integrations → ⋮ (three dots) → Custom repositories**.  
   Add `https://github.com/Sanji78/lepro_led` as **Category: Integration**.
3. Find **Lepro LED** in HACS and click **Download**.
4. **Restart** Home Assistant.

### Option B — Manual
1. Copy the folder `custom_components/lepro_led` from this repository into your Home Assistant config folder:
   - `<config>/custom_components/lepro_led`
2. **Restart** Home Assistant.

---

## ⚙️ Configuration

1. Home Assistant → **Settings → Devices & services → Add Integration**.
2. Search for **Lepro LED**.
3. Enter your **Lepro email and password**.
4. On success, entities will be created for each device.

### Options
- `region`: Europe, United States, North America, Far East
- `language`: includes `en`, `it`, and `ja`

### Entities
- **Lights**: control on/off, brightness, color temperature, RGB color, effects.
- **Sensors**: connection status, device model, firmware, MAC, online/offline.
- **Buttons**: (if applicable, e.g., factory reset or effect presets).

> Notes:
> - Credentials are stored in Home Assistant’s config entries.
> - The integration communicates with Lepro’s cloud API (internet required).

---

## B1 Notes

This fork includes dedicated investigation work for **B1** bulbs.
The B1 protocol does not behave like strip-focused `d50` control, so support has been implemented by comparing Home Assistant traffic with the official app's MQTT payloads.

### Current direction
- RGB mode is driven by `d2=1` plus `d5`.
- White/static mode is driven by `d2=0` plus `d3` and `d4`.
- Some B1 behavior is still under active investigation and may require more protocol tuning.

### Observed B1 protocol fields

| Field | Observed meaning | Notes |
| --- | --- | --- |
| `d1` | Power state | `1` on, `0` off |
| `d2` | Mode | `0` = white/static, `1` = RGB |
| `d3` | White brightness | In white mode, app writes values like `250`, `500`, `750`, `1000` |
| `d4` | White-mode companion value | Observed as `500` in app-driven white mode |
| `d5` | RGB payload body | Observed format: `{hue_hex}{03E8}{value_hex}` |
| `d30` | Extra device state field | Seen in reports, but not required by the official app's RGB writes |
| `d52` | Generic brightness field | Used by other devices/modes, but not the app's main B1 white write path |

### Observed B1 examples

| Scenario | App payload |
| --- | --- |
| Red 100% | `{'d2': 1, 'd5': '000003E803E8'}` |
| Green 100% | `{'d2': 1, 'd5': '007803E803E8'}` |
| Blue 100% | `{'d2': 1, 'd5': '00F003E803E8'}` |
| White 25% | `{'d2': 0, 'd3': 250, 'd4': 500}` |
| White 50% | `{'d2': 0, 'd3': 500, 'd4': 500}` |
| White 75% | `{'d2': 0, 'd3': 750, 'd4': 500}` |
| White 100% | `{'d2': 0, 'd3': 1000, 'd4': 500}` |

These values were captured from the official app via MQTT logging and are included here so future protocol work has a stable reference.

---

## 🧪 Supported versions
- Home Assistant: **2024.8** or newer (earlier may work, untested).

---

## 🐞 Troubleshooting
- Check **Settings → System → Logs** for messages under `custom_components.lepro_led`.
- If login fails, verify email/password by signing into the official Lepro app.
- If entities don’t update, ensure Home Assistant can reach the internet.

---

## 🙌 Contributing
PRs and issues are welcome. Please open an issue with logs if you hit a bug.

---

## ❤️ Donate
If this project helps you, consider buying me a coffee:  
**[PayPal](https://www.paypal.me/elenacapasso80)**.

..and yes... 😊 the paypal account is correct. Thank you so much!

---

## 📜 License
[MIT](LICENSE.md)

