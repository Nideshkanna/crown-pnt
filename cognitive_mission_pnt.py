#!/usr/bin/env python3
"""
cognitive_mission_pnt.py
Updated: Precise Ground Truth set to User Coordinates (12.9716, 80.0440).
"""

import time
import threading
import numpy as np
from flask import Flask, render_template_string, jsonify
from skyfield.api import load, EarthSatellite, wgs84
from skyfield.framelib import itrs
import random
from datetime import datetime, timedelta
import math

# =====================================================
# OPTIONAL RTL-SDR SUPPORT (SAFE FOR CLOUD DEPLOYMENT)
# =====================================================
try:
    from rtlsdr import RtlSdr
    RTL_AVAILABLE = True
except Exception:
    RtlSdr = None
    RTL_AVAILABLE = False

# ==========================================
# 1. CONFIGURATION (PRECISE LOCK)
# ==========================================

TRUE_LAT = 12.9706089
TRUE_LON = 80.0431389
TRUE_ALT = 45.0  # meters
SPEED_OF_LIGHT = 299792.458  # km/s

app = Flask(__name__)

# BACKUP TLEs (Guarantees visual lock even if internet fails)
EMBEDDED_TLES = """
NOAA 19
1 33591U 09005A   24068.49474772  .00000216  00000-0  16386-3 0  9993
2 33591  99.0396 244.6427 0013346 179.9299 180.1884 14.12658828779632
METEOR-M2 3
1 57166U 23091A   24068.51373977  .00000293  00000-0  18196-3 0  9997
2 57166  98.7492 195.4831 0003022  91.6033 268.5539 14.21987627 34812
METEOR-M2 4
1 59051U 24039A   24068.17936653  .00000350  00000-0  18370-3 0  9994
2 59051  98.7118 181.7946 0001439  93.1232 267.0121 14.21852467  1650
IRIDIUM 102
1 43077U 17083H   24068.45263691  .00000201  00000-0  22765-4 0  9995
2 43077  86.3958 135.2534 0002241  85.5907 274.5577 14.34217351343753
IRIDIUM 140
1 43166U 18004A   24068.45199321  .00000204  00000-0  22915-4 0  9996
2 43166  86.3955 135.2974 0002196  85.7332 274.4152 14.34216839339399
"""

state = {
    "status": "BOOTING",
    "source": "INIT",
    "sats": [],
    "fix": {"lat": 0.0, "lon": 0.0, "alt": 0, "err": 0, "mode": "INIT"},
    "log": [],
    "spectrum": [10] * 40
}

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    state['log'].append(f"[{ts}] {msg}")
    if len(state['log']) > 10:
        state['log'].pop(0)
    print(f"[{ts}] {msg}")

# ==========================================
# 2. PHYSICS ENGINE
# ==========================================
class NavEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ts = load.timescale()
        self.sdr = None
        self.sats_catalog = []
        self.init_catalog()
        self.init_radio()

    def init_catalog(self):
        log("CATALOG: Loading Embedded Core...")
        lines = EMBEDDED_TLES.strip().splitlines()
        for i in range(0, len(lines), 3):
            try:
                s = EarthSatellite(lines[i+1], lines[i+2], lines[i], self.ts)
                self.sats_catalog.append(s)
            except Exception:
                pass
        state['source'] = "EMBEDDED"
        threading.Thread(target=self.update_catalog_network, daemon=True).start()

    def update_catalog_network(self):
        time.sleep(2)
        urls = [
            'https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle',
            'https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium&FORMAT=tle',
            'https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle'
        ]
        log("NET: Connecting to Deep Space Network...")
        new_sats = []
        for url in urls:
            try:
                ns = load.tle_file(url, reload=True)
                new_sats += ns
                log(f"NET: Acquired {len(ns)} targets")
            except Exception:
                pass
        if len(new_sats) > 10:
            self.sats_catalog = new_sats
            state['source'] = "LIVE NETWORK"
            log(f"CATALOG: Updated to {len(self.sats_catalog)} Live Targets.")

    def init_radio(self):
        if not RTL_AVAILABLE:
            log("RF: RTL-SDR not available (Cloud / Simulation Mode).")
            self.sdr = None
            return
        try:
            self.sdr = RtlSdr()
            self.sdr.sample_rate = 2.048e6
            self.sdr.center_freq = 137.1e6
            self.sdr.gain = 'auto'
            log("RF: RTL-SDR Connected (Local Mode).")
        except Exception:
            log("RF: RTL-SDR init failed. Falling back to simulation.")
            self.sdr = None

    def run(self):
        log(f"INIT: Target Lock set to {TRUE_LAT:.6f}, {TRUE_LON:.6f}")
        while True:
            state['spectrum'] = (
                [random.randint(10, 50) for _ in range(40)]
                if not self.sdr else state['spectrum']
            )
            time.sleep(0.5)

# ==========================================
# 3. HUD INTERFACE (FULL UI)
# ==========================================
HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CROWN PNT MISSION CONTROL</title>
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
    <style>
        body { margin:0; background:black; color:#00ff00; font-family: monospace; }
        #map { height:100vh; width:100vw; }
    </style>
</head>
<body>
<div id="map"></div>
<script>
    var map = L.map('map').setView([12.9716, 80.0440], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
    setInterval(() => fetch('/data'), 1000);
</script>
</body>
</html>
"""

# ==========================================
# 4. WEB SERVER ROUTES (FIXED)
# ==========================================
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/data')
def data():
    return jsonify(state)

if __name__ == '__main__':
    NavEngine().start()
    print("[SERVER] Mission Control Live: http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000)

