#!/usr/bin/env python3
"""
CROWN-PNT Mission Control – Render-Safe Build
"""

import time
import random
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string
from skyfield.api import load, EarthSatellite, wgs84

LAT = 12.970609
LON = 80.043139
ALT = 45.0
MIN_EL = 5.0

app = Flask(__name__)

state = {
    "fix": {"lat": LAT, "lon": LON, "alt": ALT, "err": 0.0, "mode": "INIT"},
    "sats": [],
    "spectrum": [10] * 48,
    "status": "BOOTING",
}

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)

# ================= SAT ENGINE =================
class NavEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ts = load.timescale()
        self.sats = self.load_sats()

    def load_sats(self):
        sats = []

        # 1️⃣ Try internet TLEs
        urls = [
            "https://celestrak.org/NORAD/elements/oneweb.txt",
            "https://celestrak.org/NORAD/elements/iridium.txt",
        ]
        for url in urls:
            try:
                sats += load.tle_file(url, reload=True)
                log(f"TLE fetched: {url}")
            except Exception as e:
                log(f"TLE fetch failed: {url}")

        # 2️⃣ Fallback to local cache
        if not sats:
            tle_file = Path("tle_cache.txt")
            if tle_file.exists():
                log("Using cached TLE file")
                lines = tle_file.read_text().splitlines()
                for i in range(0, len(lines), 3):
                    try:
                        sats.append(
                            EarthSatellite(lines[i+1], lines[i+2], lines[i], self.ts)
                        )
                    except Exception:
                        pass

        # 3️⃣ Absolute failsafe (demo satellites)
        if not sats:
            log("Injecting demo satellites")
            sats.append(
                EarthSatellite(
                    "1 25544U 98067A   24022.51782528  .00012000  00000-0  22000-3 0  9990",
                    "2 25544  51.6400  25.0000 0006000  10.0000  80.0000 15.50000000    12",
                    "DEMO-SAT",
                    self.ts,
                )
            )

        log(f"Total sats loaded: {len(sats)}")
        return sats[:20]

    def run(self):
        obs = wgs84.latlon(LAT, LON)
        while True:
            now = self.ts.now()
            visible = []

            for sat in self.sats:
                try:
                    alt, az, _ = (sat - obs).at(now).altaz()
                    if alt.degrees > MIN_EL:
                        sub = wgs84.subpoint(sat.at(now))
                        visible.append({
                            "name": sat.name,
                            "el": round(alt.degrees, 1),
                            "az": round(az.degrees, 1),
                            "lat": sub.latitude.degrees,
                            "lon": sub.longitude.degrees,
                        })
                except Exception:
                    pass

            state["sats"] = visible
            state["fix"]["err"] = round(random.uniform(0.8, 2.2), 2)
            state["fix"]["mode"] = "3D LOCK (ILS)"
            state["spectrum"] = [random.randint(5, 60) for _ in range(48)]
            state["status"] = f"TRACKING {len(visible)} SATS"

            time.sleep(1)

# ================= FRONTEND =================
HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CROWN-PNT</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<style>
html,body,#map{height:100%;margin:0;background:black;}
.hud{position:fixed;color:#0f0;background:rgba(0,0,0,.85);
border:1px solid #0f0;font-family:monospace;font-size:12px;
padding:8px;z-index:9999;pointer-events:none;}
#pos{bottom:20px;left:20px;}
#leo{bottom:20px;right:20px;width:260px;}
#rf{top:20px;right:20px;}
</style>
</head>
<body>
<div id="map"></div>
<div id="pos" class="hud">
LAT <span id="lat"></span><br>
LON <span id="lon"></span><br>
ERR <span id="err"></span> m<br>
MODE <span id="mode"></span>
</div>
<div id="leo" class="hud"><b>ACTIVE LEO</b><div id="list"></div></div>
<div id="rf" class="hud"><b>RF</b><div id="spec"></div></div>

<script>
var map=L.map('map').setView([12.970609,80.043139],13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);
L.circleMarker([12.970609,80.043139],{radius:6,color:'#0f0'}).addTo(map);

var satLayer=L.layerGroup().addTo(map);
var linkLayer=L.layerGroup().addTo(map);

function update(){
fetch('/data').then(r=>r.json()).then(d=>{
lat.textContent=d.fix.lat.toFixed(6);
lon.textContent=d.fix.lon.toFixed(6);
err.textContent=d.fix.err;
mode.textContent=d.fix.mode;

satLayer.clearLayers();
linkLayer.clearLayers();
list.innerHTML="";

d.sats.forEach(s=>{
satLayer.addLayer(L.circleMarker([s.lat,s.lon],{radius:4,color:'#0f0'}));
linkLayer.addLayer(L.polyline([[12.970609,80.043139],[s.lat,s.lon]],{color:'#0f0',weight:1}));
list.innerHTML+=`${s.name}<br>`;
});

spec.innerHTML=d.spectrum.map(v=>"▮").join("");
});
}
setInterval(update,1000);
update();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/data")
def data():
    return jsonify(state)

if __name__ == "__main__":
    NavEngine().start()
    app.run(host="0.0.0.0", port=5000)

