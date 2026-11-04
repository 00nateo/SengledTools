# Sengled Lights (web)

A local web app for your Sengled Wi-Fi bulbs: pick bulbs, power, brightness and
white-temperature sliders, a color wheel with presets, Wi-Fi setup, and
control of several bulbs at once. No cloud.

## Run

```bash
cd web
npm install        # first time only
npm run dev        # starts the bulb service + the web app
```

Open http://localhost:3000.

For everyday use, run the production build instead. It uses about a third of
the memory (no compiler or hot reload kept in memory):

```bash
npm run build      # again after changing the web app's code
npm start          # same as dev: bulb service + web app on :3000
```

To run it around the clock on a Raspberry Pi that hosts the bulbs' own Wi-Fi network,
follow the step-by-step guide in `../pi/setup-guide.html` (installer: `../pi/install.sh`).

`npm run dev` starts two things:

- **Bulb service**: `python3 ../sengled_ctl.py api` on 127.0.0.1:8765. It runs the
  MQTT server that Wi-Fi bulbs connect to (port 8883) and talks UDP to bulbs in
  setup mode. It re-launches itself with the repo's `.venv` for the MQTT parts.
- **Web app**: Next.js on :3000, which proxies `/api/*` to the service.

Bulbs on Wi-Fi only take commands while the service is running, and they
reconnect about a minute after it starts. This Mac must be on the same network
as the bulbs, at the address they were set up with (see Wi-Fi setup).

## Logs

Everything the web app, TUI and commands do is appended to
`~/.sengled/logs/sengled.log` (rolled over to `sengled.log.1` at 5 MB):
commands sent, bulb replies, MQTT connections opening/closing, reconnects,
reboots (from the bulb's uptime), Wi-Fi signal, and MQTT server errors.

    tail -f ~/.sengled/logs/sengled.log

## Same features from the terminal

`python3 ../sengled_ctl.py` (TUI) and its one-shot commands share the same
saved bulbs (`~/.sengled/lan.json`). They can run alongside the web app:
whichever starts second reuses the first one's MQTT server.
