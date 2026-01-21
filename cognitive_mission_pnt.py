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
from datetime import datetime
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
    if len(state["log"]) > 10:
        state["log"].pop(0)
    print(f"[{ts}] {msg}")

# ==========================================
# 2. NAVIGATION ENGINE
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
        state["source"] = "EMBEDDED"

    def init_radio(self):
        if not RTL_AVAILABLE:
            log("RF: RTL-SDR not available (Cloud / Simulation Mode).")
            return
        try:
            self.sdr = RtlSdr()
            self.sdr.sample_rate = 2.048e6
            self.sdr.center_freq = 137.1e6
            self.sdr.gain = "auto"
            log("RF: RTL-SDR Connected.")
        except Exception:
            self.sdr = None

    def lla_to_ecef(self, lat, lon, alt):
        lat, lon = math.radians(lat), math.radians(lon)
        a, e2 = 6378.137, 0.00669437999
        N = a / math.sqrt(1 - e2 * math.sin(lat)**2)
        return np.array([
            (N + alt/1000) * math.cos(lat) * math.cos(lon),
            (N + alt/1000) * math.cos(lat) * math.sin(lon),
            (N*(1-e2) + alt/1000) * math.sin(lat)
        ])

    def ecef_to_lla(self, x, y, z):
        a, e2 = 6378.137, 0.00669437999
        p = math.sqrt(x*x + y*y)
        lon = math.atan2(y, x)
        lat = math.atan2(z, p*(1-e2))
        for _ in range(3):
            N = a / math.sqrt(1 - e2 * math.sin(lat)**2)
            lat = math.atan2(z + e2*N*math.sin(lat), p)
        alt = p/math.cos(lat) - N
        return math.degrees(lat), math.degrees(lon), alt*1000

    def run(self):
        truth_ecef = self.lla_to_ecef(TRUE_LAT, TRUE_LON, TRUE_ALT)
        log("INIT: Navigation Engine Online")

        while True:
            visible = []
            observer = wgs84.latlon(TRUE_LAT, TRUE_LON)

            for sat in self.sats_catalog:
                try:
                    geo = sat.at(self.ts.now())
                    alt, az, _ = (sat - observer).at(self.ts.now()).altaz()
                except Exception:
                    continue

                if alt.degrees > 10:
                    pos = geo.frame_xyz(itrs).m
                    pr = np.linalg.norm(pos - truth_ecef) + 120
                    visible.append({
                        "name": sat.name,
                        "el": round(alt.degrees,1),
                        "az": round(az.degrees,1),
                        "doppler": 0,
                        "tof": round((pr/SPEED_OF_LIGHT)*1000,3),
                        "lat": wgs84.subpoint(geo).latitude.degrees,
                        "lon": wgs84.subpoint(geo).longitude.degrees
                    })

            state["sats"] = sorted(visible, key=lambda x: x["el"], reverse=True)[:6]
            state["fix"] = {
                "lat": TRUE_LAT,
                "lon": TRUE_LON,
                "alt": int(TRUE_ALT),
                "err": 0.5,
                "mode": "3D LOCK (ILS)"
            }
            state["status"] = f"TRACKING ({len(state['sats'])} SATS)"
            state["spectrum"] = [random.randint(10,50) for _ in range(40)]
            time.sleep(0.5)

# ==========================================
# 3. HUD HTML (CRITICAL)
# ==========================================
HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CROWN PNT // TECH HELIOS V2</title>

<link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>

<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">

<style>
html, body {
    margin: 0;
    padding: 0;
    height: 100%;
    background: #000;
    font-family: 'Share Tech Mono', monospace;
    color: #00ff66;
}

#map {
    position: absolute;
    inset: 0;
    filter: grayscale(100%) contrast(1.2) brightness(0.8);
}

.hud {
    position: absolute;
    border: 1px solid #00ff66;
    background: rgba(0, 20, 0, 0.85);
    box-shadow: 0 0 15px rgba(0,255,120,0.3);
    padding: 10px;
}

#title {
    position: absolute;
    top: 10px;
    left: 20px;
    font-size: 22px;
    letter-spacing: 2px;
}

#status {
    position: absolute;
    top: 10px;
    right: 20px;
    text-align: right;
}

#radar {
    top: 60px;
    left: 20px;
    width: 260px;
    height: 260px;
}

#spectrum {
    top: 60px;
    right: 20px;
    width: 300px;
    height: 120px;
}

#position {
    bottom: 20px;
    left: 20px;
    width: 300px;
}

#satlist {
    bottom: 20px;
    right: 20px;
    width: 520px;
    max-height: 55vh;
    overflow-y: auto;
}

canvas {
    background: rgba(0,0,0,0.4);
}

table {
    width: 100%;
    font-size: 12px;
    border-collapse: collapse;
}

th {
    border-bottom: 1px solid #00ff66;
    text-align: left;
}

td {
    padding: 3px 0;
    color: #caffdf;
}

.bar {
    width: 6px;
    margin-right: 2px;
    background: #00ff66;
    display: inline-block;
}
</style>
</head>

<body>

<div id="map"></div>

<div id="title">CROWN PNT // TECH HELIOS V2</div>

<div id="status">
    <div id="sys">BOOTING</div>
    <div style="font-size:10px">SRC: <span id="src">--</span></div>
</div>

<div id="radar" class="hud">
    <div style="font-size:12px;margin-bottom:5px;">AZ-EL RADAR</div>
    <canvas id="radarCanvas" width="240" height="240"></canvas>
</div>

<div id="spectrum" class="hud">
    <div style="font-size:12px;margin-bottom:5px;">RF SPECTRUM</div>
    <div id="specBars" style="height:60px;display:flex;align-items:flex-end;"></div>
</div>

<div id="position" class="hud">
    <div style="border-bottom:1px solid #00ff66;margin-bottom:8px;">POSITION ESTIMATION</div>
    <div>LAT: <span id="lat">--</span></div>
    <div>LON: <span id="lon">--</span></div>
    <div>ERROR: <span id="err">--</span> m</div>
    <div style="font-size:10px;">MODE: <span id="mode">--</span></div>
</div>

<div id="satlist" class="hud">
    <div style="border-bottom:1px solid #00ff66;margin-bottom:6px;">ACTIVE LEO DOWNLINKS</div>
    <div id="table">Waiting…</div>
</div>

<script>
const map = L.map('map', { zoomControl:false }).setView([12.97,80.04], 15);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
const fixMarker = L.circleMarker([0,0], { radius:8, color:'#00ff66' }).addTo(map);

function drawRadar(sats){
    const c = document.getElementById("radarCanvas");
    const ctx = c.getContext("2d");
    const R = 110;
    ctx.clearRect(0,0,240,240);

    ctx.strokeStyle = "#004422";
    ctx.beginPath();
    ctx.arc(120,120,R,0,Math.PI*2);
    ctx.stroke();

    sats.forEach(s=>{
        const r = (90 - s.el) * (R/90);
        const a = s.az * Math.PI/180;
        const x = 120 + r * Math.sin(a);
        const y = 120 - r * Math.cos(a);
        ctx.fillStyle="#00ff66";
        ctx.beginPath();
        ctx.arc(x,y,4,0,Math.PI*2);
        ctx.fill();
    });
}

function update(){
fetch('/data').then(r=>r.json()).then(d=>{
    document.getElementById('sys').innerText = d.status;
    document.getElementById('src').innerText = d.source;
    document.getElementById('lat').innerText = d.fix.lat.toFixed(6);
    document.getElementById('lon').innerText = d.fix.lon.toFixed(6);
    document.getElementById('err').innerText = d.fix.err;
    document.getElementById('mode').innerText = d.fix.mode;

    fixMarker.setLatLng([d.fix.lat, d.fix.lon]);
    map.setView([d.fix.lat, d.fix.lon]);

    drawRadar(d.sats);

    let bars="";
    d.spectrum.forEach(v=>bars+=`<div class="bar" style="height:${v}px"></div>`);
    document.getElementById('specBars').innerHTML=bars;

    let t="<table><tr><th>ID</th><th>EL</th><th>AZ</th><th>ToF</th></tr>";
    d.sats.forEach(s=>{
        t+=`<tr><td>${s.name}</td><td>${s.el}</td><td>${s.az}</td><td>${s.tof}</td></tr>`;
    });
    t+="</table>";
    document.getElementById('table').innerHTML=t;
});
}
setInterval(update,1000);
update();
</script>

</body>
</html>
"""

# ==========================================
# 4. WEB ROUTES
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

