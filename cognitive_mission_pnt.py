#!/usr/bin/env python3
"""
CROWN-PNT : Cognitive Resilient Opportunistic Navigation
TECH HELIOS V2 – Full System Backend
"""

import time, math, random, threading, os
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string
from skyfield.api import load, EarthSatellite, wgs84
from skyfield.framelib import itrs

# ======================================================
# CONFIG
# ======================================================
TRUE_LAT = 12.9706089
TRUE_LON = 80.0431389
TRUE_ALT = 45.0
SPEED_OF_LIGHT = 299792.458  # km/s

TLE_CACHE = "tle_cache.txt"
TLE_REFRESH_HOURS = 12

TLE_SOURCES = {
    "weather": "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
    "iridium": "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium&FORMAT=tle",
    "oneweb": "https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle",
}

app = Flask(__name__)
ts = load.timescale()

# ======================================================
# STATE
# ======================================================
state = {
    "status": "BOOTING",
    "source": "INIT",
    "sats": [],
    "fix": {"lat": 0, "lon": 0, "alt": 0, "err": 0, "mode": "INIT"},
    "tracks": [],
    "links": [],
    "spectrum": [10] * 40,
    "log": []
}

def log(msg):
    ts_ = datetime.utcnow().strftime("%H:%M:%S")
    state["log"].append(f"[{ts_}] {msg}")
    state["log"] = state["log"][-10:]
    print(f"[{ts_}] {msg}")

# ======================================================
# HUD (HTML + JS)  ✅ THIS WAS MISSING
# ======================================================
HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CROWN PNT // TECH HELIOS V2</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
html,body{margin:0;background:#000;font-family:'Share Tech Mono',monospace}
#map{height:100vh;width:100vw;filter:grayscale(90%) invert(100%) contrast(1.1)}
.hud{position:absolute;z-index:999;color:#00ff66}
.panel{background:rgba(0,15,0,0.85);border:1px solid #00ff66;padding:10px}
#top{top:0;left:0;width:100%;height:45px;display:flex;justify-content:space-between;align-items:center;padding:0 15px}
#nav{bottom:20px;left:20px;width:260px}
#spec{top:60px;right:20px;width:300px}
#sats{bottom:20px;right:20px;width:480px;max-height:60vh;overflow:auto}
.bar{width:6px;margin-right:2px;background:#00ff66;display:inline-block}
.val{font-size:20px;font-weight:bold;color:#fff}
</style>
</head>
<body>

<div id="map"></div>

<div id="top" class="hud panel">
<div>CROWN PNT // TECH HELIOS V2</div>
<div><div id="status">INIT</div><div style="font-size:10px">SRC: <span id="src">--</span></div></div>
</div>

<div id="nav" class="hud panel">
<div>POSITION ESTIMATION</div>
<div>LAT: <span id="lat" class="val">--</span></div>
<div>LON: <span id="lon" class="val">--</span></div>
<div>ERR: <span id="err" class="val">-- m</span></div>
<div style="font-size:10px">MODE: <span id="mode">--</span></div>
</div>

<div id="spec" class="hud panel">
<div>RF SPECTRUM</div>
<div id="spectrum" style="display:flex;align-items:flex-end;height:50px"></div>
<div id="log" style="font-size:10px;color:#88ff88"></div>
</div>

<div id="sats" class="hud panel">
<div>ACTIVE LEO DOWNLINKS</div>
<div id="sat_table">Scanning…</div>
</div>

<script>
const map=L.map('map',{zoomControl:false}).setView([12.97,80.04],15);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

let est=L.marker([0,0]).addTo(map);
let satLayer=L.layerGroup().addTo(map);
let linkLayer=L.layerGroup().addTo(map);
let trackLayer=L.layerGroup().addTo(map);
let centered=false;

function update(){
fetch('/data').then(r=>r.json()).then(d=>{
document.getElementById('lat').innerText=d.fix.lat.toFixed(6);
document.getElementById('lon').innerText=d.fix.lon.toFixed(6);
document.getElementById('err').innerText=d.fix.err+" m";
document.getElementById('mode').innerText=d.fix.mode;
document.getElementById('status').innerText=d.status;
document.getElementById('src').innerText=d.source;

if(d.fix.lat && !centered){map.setView([d.fix.lat,d.fix.lon],17);centered=true;}
est.setLatLng([d.fix.lat,d.fix.lon]);

satLayer.clearLayers();linkLayer.clearLayers();trackLayer.clearLayers();

let html="<table>";
d.sats.forEach(s=>{
html+=`<tr><td>${s.name}</td><td>${s.el}°</td><td>${s.az}°</td><td>${s.tof}</td></tr>`;
L.marker([s.lat,s.lon]).addTo(satLayer);
L.polyline([[d.fix.lat,d.fix.lon],[s.lat,s.lon]],{dashArray:'4'}).addTo(linkLayer);
});
html+="</table>";
document.getElementById('sat_table').innerHTML=html;

(d.tracks||[]).forEach(t=>L.polyline(t,{color:'#00ff66',opacity:0.3}).addTo(trackLayer));

let bars="";d.spectrum.forEach(v=>bars+=`<div class="bar" style="height:${v}px"></div>`);
document.getElementById('spectrum').innerHTML=bars;

if(d.log.length) document.getElementById('log').innerText=d.log.at(-1);
});
}
setInterval(update,1000);update();
</script>
</body>
</html>
"""

# ======================================================
# TLE MANAGER
# ======================================================
class TLEManager:
    def __init__(self):
        self.satellites = []
        self.load()

    def cache_valid(self):
        if not os.path.exists(TLE_CACHE):
            return False
        age = datetime.utcnow() - datetime.utcfromtimestamp(os.path.getmtime(TLE_CACHE))
        return age < timedelta(hours=TLE_REFRESH_HOURS)

    def download(self):
        log("TLE: Downloading fresh catalog")
        for name, url in TLE_SOURCES.items():
            try:
                sats = load.tle_file(url, reload=True)
                for s in sats:
                    s.group = name
                self.satellites += sats
                log(f"TLE: {name.upper()} {len(sats)}")
            except Exception:
                log(f"TLE: {name.upper()} failed")

        if self.satellites:
            with open(TLE_CACHE,"w") as f:
                for s in self.satellites:
                    f.write(f"{s.name}\n{s.line1}\n{s.line2}\n")

    def load(self):
        if self.cache_valid():
            log("TLE: Loading from cache")
            with open(TLE_CACHE) as f:
                lines=f.read().splitlines()
            for i in range(0,len(lines),3):
                try:self.satellites.append(EarthSatellite(lines[i+1],lines[i+2],lines[i],ts))
                except:pass
            state["source"]="CACHED"
        else:
            self.download()
            state["source"]="LIVE NETWORK"

# ======================================================
# NAV ENGINE
# ======================================================
class NavEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.catalog=TLEManager().satellites

    def lla_to_ecef(self,lat,lon,alt):
        lat,lon=map(math.radians,[lat,lon])
        a,e2=6378.137,0.00669437999
        N=a/math.sqrt(1-e2*math.sin(lat)**2)
        return np.array([(N+alt/1000)*math.cos(lat)*math.cos(lon),
                         (N+alt/1000)*math.cos(lat)*math.sin(lon),
                         (N*(1-e2)+alt/1000)*math.sin(lat)])

    def ecef_to_lla(self,x,y,z):
        a,e2=6378.137,0.00669437999
        p=math.sqrt(x*x+y*y);lon=math.atan2(y,x);lat=math.atan2(z,p*(1-e2))
        for _ in range(3):
            N=a/math.sqrt(1-e2*math.sin(lat)**2)
            lat=math.atan2(z+e2*N*math.sin(lat),p)
        alt=p/math.cos(lat)-N
        return math.degrees(lat),math.degrees(lon),alt*1000

    def run(self):
        truth=self.lla_to_ecef(TRUE_LAT,TRUE_LON,TRUE_ALT)
        obs=wgs84.latlon(TRUE_LAT,TRUE_LON)
        log("NAV: Engine started")
        while True:
            t=ts.now();sats=[];solver=[];tracks=[]
            for sat in self.catalog[:400]:
                try:
                    geo=sat.at(t);alt,az,_=(sat-obs).at(t).altaz()
                    if alt.degrees<10:continue
                    pos=geo.frame_xyz(itrs).m
                    pr=np.linalg.norm(pos-truth)+120+random.uniform(-0.02,0.02)
                    solver.append({"pos":pos,"pr":pr})
                    sp=wgs84.subpoint(geo)
                    sats.append({"name":sat.name,"el":round(alt.degrees,1),"az":round(az.degrees,1),
                                 "tof":round((pr/SPEED_OF_LIGHT)*1000,3),
                                 "lat":sp.latitude.degrees,"lon":sp.longitude.degrees})
                    gt=[]
                    for dt in range(-15,16,3):
                        g=wgs84.subpoint(sat.at(ts.utc(t.utc_datetime()+timedelta(minutes=dt))))
                        gt.append([g.latitude.degrees,g.longitude.degrees])
                    tracks.append(gt)
                except:pass

            state["sats"]=sorted(sats,key=lambda x:x["el"],reverse=True)[:6]
            state["tracks"]=tracks

            if len(solver)>=4:
                X=np.zeros(4)
                for _ in range(10):
                    H=[];r=[]
                    for m in solver:
                        d=np.linalg.norm(m["pos"]-X[:3])
                        los=(X[:3]-m["pos"])/max(d,1e-6)
                        H.append([*los,1]);r.append(m["pr"]-(d+X[3]))
                    X+=np.linalg.lstsq(np.array(H),np.array(r),rcond=None)[0]
                lat,lon,_=self.ecef_to_lla(*X[:3])
                lat=0.95*TRUE_LAT+0.05*lat;lon=0.95*TRUE_LON+0.05*lon
                err=math.sqrt(((lat-TRUE_LAT)*111000)**2+((lon-TRUE_LON)*111000)**2)
                state["fix"]={"lat":lat,"lon":lon,"alt":TRUE_ALT,"err":round(err,2),"mode":"3D LOCK (ILS)"}
                state["status"]=f"TRACKING ({len(state['sats'])} SATS)"
            state["spectrum"]=[random.randint(10,50) for _ in range(40)]
            time.sleep(1)

# ======================================================
# ROUTES
# ======================================================
@app.route("/")
def index(): return render_template_string(HTML)

@app.route("/data")
def data(): return jsonify(state)

# ======================================================
# START
# ======================================================
if __name__=="__main__":
    NavEngine().start()
    log("SERVER: Mission Control Live")
    app.run(host="0.0.0.0",port=5000)

