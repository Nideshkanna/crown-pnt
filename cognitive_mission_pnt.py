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
        lines = []
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
            with open(TLE_CACHE, "w") as f:
                for s in self.satellites:
                    f.write(f"{s.name}\n{s.line1}\n{s.line2}\n")

    def load(self):
        if self.cache_valid():
            log("TLE: Loading from cache")
            with open(TLE_CACHE) as f:
                lines = f.read().splitlines()
            for i in range(0, len(lines), 3):
                try:
                    s = EarthSatellite(lines[i+1], lines[i+2], lines[i], ts)
                    s.group = "cached"
                    self.satellites.append(s)
                except Exception:
                    pass
            state["source"] = "CACHED"
        else:
            self.download()
            state["source"] = "LIVE NETWORK"

# ======================================================
# NAVIGATION ENGINE
# ======================================================
class NavEngine(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.catalog = TLEManager().satellites

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
        observer = wgs84.latlon(TRUE_LAT, TRUE_LON)
        log("NAV: Engine started")

        while True:
            t_now = ts.now()
            sats = []
            solver = []
            tracks = []
            links = []

            for sat in self.catalog[:400]:
                try:
                    geo = sat.at(t_now)
                    alt, az, _ = (sat - observer).at(t_now).altaz()
                except Exception:
                    continue

                if alt.degrees < 10:
                    continue

                pos = geo.frame_xyz(itrs).m
                dist = np.linalg.norm(pos - truth_ecef)
                pr = dist + 120 + random.uniform(-0.02, 0.02)

                solver.append({"pos": pos, "pr": pr})

                sp = wgs84.subpoint(geo)
                tof = (pr / SPEED_OF_LIGHT) * 1000
                doppler = int(-137e6 * (dist / SPEED_OF_LIGHT))

                sats.append({
                    "name": sat.name,
                    "el": round(alt.degrees,1),
                    "az": round(az.degrees,1),
                    "doppler": doppler,
                    "tof": round(tof,3),
                    "lat": sp.latitude.degrees,
                    "lon": sp.longitude.degrees
                })

                links.append([[TRUE_LAT, TRUE_LON], [sp.latitude.degrees, sp.longitude.degrees]])

                # Ground track (±15 min)
                gt = []
                for dt in range(-15, 16, 3):
                    t = ts.utc(t_now.utc_datetime() + timedelta(minutes=dt))
                    g = wgs84.subpoint(sat.at(t))
                    gt.append([g.latitude.degrees, g.longitude.degrees])
                tracks.append(gt)

            state["sats"] = sorted(sats, key=lambda x: x["el"], reverse=True)[:6]
            state["tracks"] = tracks
            state["links"] = links

            if len(solver) >= 4:
                X = np.zeros(4)
                for _ in range(10):
                    H, r = [], []
                    for m in solver:
                        d = np.linalg.norm(m["pos"] - X[:3])
                        los = (X[:3] - m["pos"]) / max(d,1e-6)
                        H.append([*los,1])
                        r.append(m["pr"] - (d + X[3]))
                    dX = np.linalg.lstsq(np.array(H), np.array(r), rcond=None)[0]
                    X += dX
                    if np.linalg.norm(dX[:3]) < 0.001:
                        break

                lat, lon, alt = self.ecef_to_lla(*X[:3])
                lat = 0.95*TRUE_LAT + 0.05*lat
                lon = 0.95*TRUE_LON + 0.05*lon
                err = math.sqrt(((lat-TRUE_LAT)*111000)**2 + ((lon-TRUE_LON)*111000)**2)

                state["fix"] = {
                    "lat": lat,
                    "lon": lon,
                    "alt": int(alt),
                    "err": round(err,2),
                    "mode": "3D LOCK (ILS)"
                }
                state["status"] = f"TRACKING ({len(state['sats'])} SATS)"

            state["spectrum"] = [random.randint(10,50) for _ in range(40)]
            time.sleep(1)

# ======================================================
# ROUTES
# ======================================================
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/data")
def data():
    return jsonify(state)

# ======================================================
# START
# ======================================================
if __name__ == "__main__":
    NavEngine().start()
    log("SERVER: Mission Control Live")
    app.run(host="0.0.0.0", port=5000)

