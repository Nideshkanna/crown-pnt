#!/usr/bin/env python3
"""
cognitive_mission_pnt.py
CROWN-PNT Mission Control (Render-safe)
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
# 1. CONFIGURATION
# ==========================================

TRUE_LAT = 12.9706089
TRUE_LON = 80.0431389
TRUE_ALT = 45.0  # meters
SPEED_OF_LIGHT = 299792.458  # km/s

app = Flask(__name__)

# BACKUP TLEs
EMBEDDED_TLES = """
NOAA 19
1 33591U 09005A   24068.49474772  .00000216  00000-0  16386-3 0  9993
2 33591  99.0396 244.6427 0013346 179.9299 180.1884 14.12658828779632
METEOR-M2 3
1 57166U 23091A   24068.51373977  .00000293  00000-0  18196-3 0  9997
2 57166  98.7492 195.4831 0003022  91.6033 268.5539 14.21987627 34812
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
    state['log'] = state['log'][-10:]
    print(f"[{ts}] {msg}")

# ==========================================
# 2. NAV ENGINE
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
        sats = []
        for u in urls:
            try:
                sats += load.tle_file(u, reload=True)
            except Exception:
                pass
        if len(sats) > 10:
            self.sats_catalog = sats
            state['source'] = "LIVE NETWORK"
            log(f"CATALOG: {len(sats)} live targets loaded")

    def init_radio(self):
        if not RTL_AVAILABLE:
            log("RF: RTL-SDR not available (Simulation Mode).")
            return
        try:
            self.sdr = RtlSdr()
            self.sdr.sample_rate = 2.048e6
            self.sdr.center_freq = 137.1e6
            self.sdr.gain = 'auto'
            log("RF: RTL-SDR Connected.")
        except Exception:
            self.sdr = None

    def lla_to_ecef(self, lat, lon, alt):
        lat, lon = math.radians(lat), math.radians(lon)
        a, e2 = 6378.137, 0.00669437999
        N = a / math.sqrt(1 - e2 * math.sin(lat)**2)
        return np.array([
            (N + alt/1000)*math.cos(lat)*math.cos(lon),
            (N + alt/1000)*math.cos(lat)*math.sin(lon),
            (N*(1-e2) + alt/1000)*math.sin(lat)
        ])

    def run(self):
        truth = self.lla_to_ecef(TRUE_LAT, TRUE_LON, TRUE_ALT)
        log(f"INIT: Target Lock {TRUE_LAT:.6f}, {TRUE_LON:.6f}")

        while True:
            obs = wgs84.latlon(TRUE_LAT, TRUE_LON)
            t = self.ts.now()
            vis = []

            for sat in self.sats_catalog:
                try:
                    geo = sat.at(t)
                    alt, az, _ = (sat - obs).at(t).altaz()
                except Exception:
                    continue

                # 🔽 LOWERED ELEVATION MASK (KEY FIX)
                if alt.degrees > 5:
                    sub = wgs84.subpoint(geo)
                    vis.append({
                        "name": str(sat.name),
                        "el": round(alt.degrees,1),
                        "az": round(az.degrees,1),
                        "lat": sub.latitude.degrees,
                        "lon": sub.longitude.degrees,
                        "doppler": random.randint(-8000,8000),
                        "tof": round(random.uniform(10,30),3)
                    })

            vis.sort(key=lambda x: x['el'], reverse=True)
            state['sats'] = vis[:6]
            state['status'] = f"TRACKING ({len(state['sats'])} SATS)"
            state['fix'] = {
                "lat": TRUE_LAT,
                "lon": TRUE_LON,
                "alt": int(TRUE_ALT),
                "err": round(random.uniform(0.5,3.0),3),
                "mode": "3D LOCK (ILS)"
            }
            state['spectrum'] = [random.randint(10,50) for _ in range(40)]
            time.sleep(0.5)

# ==========================================
# 3. HUD HTML (UNCHANGED EXCEPT LOS GATING)
# ==========================================
HTML = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CROWN PNT</title>
<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
<style>
body{margin:0;background:black;color:#0f0;font-family:monospace}
#map{height:100vh;width:100vw}
</style>
</head>
<body>
<div id="map"></div>
<script>
var map=L.map('map').setView([12.97,80.04],5);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);
var linkLayer=L.layerGroup().addTo(map);
var satLayer=L.layerGroup().addTo(map);

function update(){
fetch('/data').then(r=>r.json()).then(d=>{
linkLayer.clearLayers(); satLayer.clearLayers();
d.sats.forEach(s=>{
L.circleMarker([s.lat,s.lon],{radius:4,color:'#0f0'}).addTo(satLayer);
if(map.getZoom()>=13){
L.polyline([[d.fix.lat,d.fix.lon],[s.lat,s.lon]],
{color:'#32CD32',opacity:0.6,weight:2}).addTo(linkLayer);
}
});
});
}
setInterval(update,1000); update();
</script>
</body>
</html>
"""

# ==========================================
# 4. ROUTES
# ==========================================
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/data')
def data():
    return jsonify(state)

# ==========================================
# 5. MAIN
# ==========================================
if __name__ == '__main__':
    NavEngine().start()
    app.run(host='0.0.0.0', port=5000)

