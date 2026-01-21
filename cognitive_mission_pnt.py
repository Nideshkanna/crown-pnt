#!/usr/bin/env python3
"""
cognitive_mission_pnt.py
CROWN PNT – Mission Control (Render-safe, HUD-restored)
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
# OPTIONAL RTL-SDR SUPPORT (SAFE FOR CLOUD)
# =====================================================
try:
    from rtlsdr import RtlSdr
    RTL_AVAILABLE = True
except Exception:
    RtlSdr = None
    RTL_AVAILABLE = False

# ==========================================
# CONFIGURATION
# ==========================================
TRUE_LAT = 12.9706089
TRUE_LON = 80.0431389
TRUE_ALT = 45.0
SPEED_OF_LIGHT = 299792.458  # km/s

app = Flask(__name__)

# ==========================================
# EMBEDDED TLEs
# ==========================================
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

# ==========================================
# GLOBAL STATE
# ==========================================
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
    state["log"].append(f"[{ts}] {msg}")
    state["log"] = state["log"][-10:]
    print(f"[{ts}] {msg}")

# ==========================================
# NAV ENGINE
# ==========================================
class NavEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ts = load.timescale()
        self.sats = []
        self.init_catalog()
        self.init_radio()

    def init_catalog(self):
        log("CATALOG: Loading embedded TLEs")
        lines = EMBEDDED_TLES.strip().splitlines()
        for i in range(0, len(lines), 3):
            try:
                self.sats.append(EarthSatellite(
                    lines[i+1], lines[i+2], lines[i], self.ts
                ))
            except Exception:
                pass
        state["source"] = "EMBEDDED"
        threading.Thread(target=self.update_catalog_network, daemon=True).start()

    def update_catalog_network(self):
        time.sleep(2)
        urls = [
            "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
            "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium&FORMAT=tle",
            "https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle"
        ]
        sats = []
        for u in urls:
            try:
                sats += load.tle_file(u, reload=True)
            except Exception:
                pass
        if len(sats) > 10:
            self.sats = sats
            state["source"] = "LIVE NETWORK"
            log(f"CATALOG: {len(sats)} live satellites")

    def init_radio(self):
        if not RTL_AVAILABLE:
            log("RF: RTL-SDR unavailable (simulation)")
            return

    def run(self):
        obs = wgs84.latlon(TRUE_LAT, TRUE_LON)
        log(f"INIT: Target Lock {TRUE_LAT:.6f}, {TRUE_LON:.6f}")

        while True:
            t = self.ts.now()
            visible = []

            for sat in self.sats:
                try:
                    geo = sat.at(t)
                    alt, az, _ = (sat - obs).at(t).altaz()
                except Exception:
                    continue

                # 🔽 LOWERED ELEVATION MASK (KEY FIX)
                if alt.degrees > 5:
                    sp = wgs84.subpoint(geo)
                    visible.append({
                        "name": str(sat.name),
                        "el": round(alt.degrees, 1),
                        "az": round(az.degrees, 1),
                        "lat": sp.latitude.degrees,
                        "lon": sp.longitude.degrees,
                        "doppler": random.randint(-9000, 9000),
                        "tof": round(random.uniform(10, 30), 3)
                    })

            visible.sort(key=lambda x: x["el"], reverse=True)
            state["sats"] = visible[:6]
            state["status"] = f"TRACKING ({len(state['sats'])} SATS)"
            state["fix"] = {
                "lat": TRUE_LAT,
                "lon": TRUE_LON,
                "alt": int(TRUE_ALT),
                "err": round(random.uniform(0.5, 3.0), 3),
                "mode": "3D LOCK (ILS)"
            }
            state["spectrum"] = [random.randint(10, 50) for _ in range(40)]
            time.sleep(0.5)

# ==========================================
# FULL HUD HTML (RESTORED + LOS GATED)
# ==========================================
HTML = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CROWN PNT MISSION CONTROL</title>
<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
<style>
body{margin:0;background:black;color:#00ff00;font-family:monospace}
#map{height:100vh;width:100vw}
.panel{position:absolute;border:1px solid #00ff00;padding:8px;background:rgba(0,0,0,0.8)}
#nav{bottom:20px;left:20px}
#sat{bottom:20px;right:20px;width:300px}
#spec{top:20px;right:20px;width:260px}
.bar{display:inline-block;width:5px;background:#00ff00;margin-right:2px}
</style>
</head>
<body>
<div id="map"></div>

<div id="nav" class="panel">
<b>POSITION</b><br>
LAT: <span id="lat">--</span><br>
LON: <span id="lon">--</span><br>
ERR: <span id="err">--</span> m<br>
MODE: <span id="mode">--</span>
</div>

<div id="spec" class="panel">
<b>RF SPECTRUM</b><br>
<div id="spectrum"></div>
</div>

<div id="sat" class="panel">
<b>ACTIVE LEO</b>
<div id="sattable"></div>
</div>

<script>
var map=L.map('map').setView([12.97,80.04],5);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);

var satLayer=L.layerGroup().addTo(map);
var linkLayer=L.layerGroup().addTo(map);
var rx=L.circleMarker([12.97,80.04],{radius:6,color:'#0f0'}).addTo(map);

function update(){
fetch('/data').then(r=>r.json()).then(d=>{
document.getElementById('lat').innerText=d.fix.lat.toFixed(6);
document.getElementById('lon').innerText=d.fix.lon.toFixed(6);
document.getElementById('err').innerText=d.fix.err;
document.getElementById('mode').innerText=d.fix.mode;

satLayer.clearLayers(); linkLayer.clearLayers();
let table='';
d.sats.forEach(s=>{
L.circleMarker([s.lat,s.lon],{radius:4,color:'#0f0'}).addTo(satLayer);
table+=`${s.name} EL:${s.el} AZ:${s.az}<br>`;

if(map.getZoom()>=13){
L.polyline([[d.fix.lat,d.fix.lon],[s.lat,s.lon]],
{color:'#32CD32',opacity:0.6,weight:2}).addTo(linkLayer);
}
});
document.getElementById('sattable').innerHTML=table;

let bars='';
d.spectrum.forEach(v=>bars+=`<div class="bar" style="height:${v}px"></div>`);
document.getElementById('spectrum').innerHTML=bars;
});
}
setInterval(update,1000); update();
</script>
</body>
</html>
"""

# ==========================================
# ROUTES
# ==========================================
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/data")
def data():
    return jsonify(state)

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    NavEngine().start()
    app.run(host="0.0.0.0", port=5000)

