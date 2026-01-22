#!/usr/bin/env python3
"""
CROWN-PNT : Reliable Demo Build (Satellite-Safe)
"""

import time, threading, math, random, requests
import numpy as np
from datetime import datetime
from flask import Flask, render_template_string, jsonify
from skyfield.api import load, EarthSatellite, wgs84
from skyfield.framelib import itrs

# ================= CONFIG =================
TRUE_LAT = 12.970609
TRUE_LON = 80.043139
TRUE_ALT = 45.0
SPEED_OF_LIGHT = 299792.458
MIN_ELEVATION = 5.0

app = Flask(__name__)

# ================= STATE ==================
state = {
    "status": "BOOTING",
    "source": "EMBEDDED",
    "sats": [],
    "fix": {
        "lat": TRUE_LAT,
        "lon": TRUE_LON,
        "alt": TRUE_ALT,
        "err": 0.0,
        "mode": "INIT"
    },
    "spectrum": [10]*48,
    "log": []
}

def log(msg):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    state["log"].append(f"[{ts}] {msg}")
    state["log"] = state["log"][-12:]
    print(msg)

# ================= EMBEDDED TLES =================
EMBEDDED_TLES = [
("NOAA 19",
"1 33591U 09005A   24068.49474772  .00000216  00000-0  16386-3 0  9993",
"2 33591  99.0396 244.6427 0013346 179.9299 180.1884 14.12658828779632"),

("METEOR-M2 3",
"1 57166U 23091A   24068.51373977  .00000293  00000-0  18196-3 0  9997",
"2 57166  98.7492 195.4831 0003022  91.6033 268.5539 14.21987627 34812"),

("IRIDIUM 102",
"1 43077U 17083H   24068.45263691  .00000201  00000-0  22765-4 0  9995",
"2 43077  86.3958 135.2534 0002241  85.5907 274.5577 14.34217351343753"),
]

# ================= NAV ENGINE ==================
class NavEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ts = load.timescale()
        self.catalog = []
        self.load_embedded()
        self.load_live_safe()

    def load_embedded(self):
        for name,l1,l2 in EMBEDDED_TLES:
            self.catalog.append(EarthSatellite(l1, l2, name, self.ts))
        log(f"Embedded satellites loaded: {len(self.catalog)}")

    def load_live_safe(self):
        urls = {
            "ONEWEB": "https://celestrak.org/NORAD/elements/oneweb.txt",
            "IRIDIUM": "https://celestrak.org/NORAD/elements/iridium.txt",
            "WEATHER": "https://celestrak.org/NORAD/elements/weather.txt"
        }

        for tag,url in urls.items():
            try:
                sats = load.tle_file(url, timeout=8)
                self.catalog.extend(sats[:25])
                log(f"{tag} loaded: {len(sats)}")
                state["source"] = "LIVE+EMBEDDED"
            except Exception as e:
                log(f"{tag} load failed, using embedded only")

        log(f"Total satellite catalog: {len(self.catalog)}")

    def run(self):
        obs = wgs84.latlon(TRUE_LAT, TRUE_LON)
        truth_ecef = np.array(obs.itrs_xyz.m)

        while True:
            now = self.ts.now()
            visible = []

            for sat in self.catalog:
                try:
                    alt, az, _ = (sat - obs).at(now).altaz()
                    if alt.degrees >= MIN_ELEVATION:
                        geo = sat.at(now)
                        sub = wgs84.subpoint(geo)
                        dist = np.linalg.norm(geo.frame_xyz(itrs).m - truth_ecef)
                        visible.append({
                            "name": sat.name,
                            "el": round(alt.degrees,1),
                            "az": round(az.degrees,1),
                            "lat": round(sub.latitude.degrees,4),
                            "lon": round(sub.longitude.degrees,4),
                            "tof": round(dist/SPEED_OF_LIGHT*1000,2)
                        })
                except:
                    pass

            visible = sorted(visible, key=lambda x: x["el"], reverse=True)[:8]
            state["sats"] = visible
            state["status"] = f"TRACKING ({len(visible)} SATS)"
            state["fix"]["mode"] = "3D LOCK (ILS)"
            state["fix"]["err"] = round(random.uniform(0.5,2.5),2)
            state["spectrum"] = [random.randint(8,55) for _ in range(48)]

            time.sleep(1)

# ================= HTML ==================
HTML = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CROWN PNT</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<style>
html,body,#map{height:100%;margin:0;}
#map{background:#000;}
.hud{
position:fixed;z-index:999;
color:#00ff00;background:rgba(0,0,0,0.85);
border:1px solid #00ff00;
font-family:monospace;
padding:8px;font-size:12px;
}
#pos{bottom:20px;left:20px;}
#sat{bottom:20px;right:20px;max-width:240px;}
#rf{top:20px;right:20px;}
</style>
</head>
<body>
<div id="map"></div>

<div id="pos" class="hud">
<b>POSITION</b><br>
LAT <span id="lat"></span><br>
LON <span id="lon"></span><br>
ERR <span id="err"></span> m<br>
MODE <span id="mode"></span>
</div>

<div id="sat" class="hud"><b>ACTIVE LEO</b><div id="sats"></div></div>
<div id="rf" class="hud"><b>RF SPECTRUM</b><div id="spec"></div></div>

<script>
var map=L.map('map',{zoomControl:true}).setView([12.970609,80.043139],13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);
var fix=L.circleMarker([12.970609,80.043139],{radius:6,color:'#00ff00'}).addTo(map);

function update(){
fetch('/data').then(r=>r.json()).then(d=>{
lat.innerText=d.fix.lat.toFixed(6);
lon.innerText=d.fix.lon.toFixed(6);
err.innerText=d.fix.err;
mode.innerText=d.fix.mode;

sats.innerHTML="";
d.sats.forEach(s=>{
sats.innerHTML+=`${s.name}<br>EL:${s.el} AZ:${s.az}<br>`;
});

spec.innerHTML=d.spectrum.map(v=>"▮").join("");
});
}
setInterval(update,1000);update();
</script>
</body>
</html>
"""

# ================= ROUTES ==================
@app.route("/")
def index(): return render_template_string(HTML)

@app.route("/data")
def data(): return jsonify(state)

# ================= MAIN ====================
if __name__ == "__main__":
    NavEngine().start()
    app.run(host="0.0.0.0", port=5000)
`/////////////////////////////////////////////////////////////////////////:
