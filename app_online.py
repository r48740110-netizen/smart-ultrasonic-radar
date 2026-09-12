try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None
import threading
import time as _serial_time

from flask import (
    Flask,
    jsonify,
    render_template_string,
    send_file,
    session,
    redirect,
    url_for,
    request
)

import pandas as pd
import numpy as np
import os
from datetime import datetime

# Online-ready architecture: browser Web Serial is the preferred USB path.
# The hosted site never needs direct access to a student USB port; the browser
# asks the student for permission and streams compatible sensor readings.


# =========================================================
# FLASK APP
# =========================================================

app = Flask(__name__)

# ---------- Arduino USB / Serial bridge ----------
serial_state = {"connected": False, "port": None, "baud": 115200,
                "last_reading": None, "last_error": None}
serial_lock = threading.Lock()
serial_conn = None
serial_thread = None
serial_stop = threading.Event()

def list_serial_ports():
    if serial is None:
        return []
    result = []
    try:
        for p in serial.tools.list_ports.comports():
            result.append({"port": p.device,
                           "description": p.description or "Serial device",
                           "manufacturer": p.manufacturer or ""})
    except Exception as e:
        serial_state["last_error"] = str(e)
    return result

def _parse_sensor_line(line):
    line = line.strip()
    if not line:
        return None
    try:
        import json as _json
        obj = _json.loads(line)
        return float(obj["angle"]), float(obj["distance"])
    except Exception:
        pass
    parts = [x.strip() for x in line.split(",")]
    if len(parts) >= 2:
        try:
            return float(parts[0]), float(parts[1])
        except Exception:
            pass
    # Optional key=value format: angle=75,distance=42
    try:
        kv = {}
        for item in parts:
            if "=" in item:
                k, v = item.split("=", 1)
                kv[k.strip().lower()] = float(v.strip())
        if "angle" in kv and "distance" in kv:
            return kv["angle"], kv["distance"]
    except Exception:
        pass
    return None

def _serial_reader():
    global serial_conn
    while not serial_stop.is_set() and serial_conn:
        try:
            raw = serial_conn.readline()
            if not raw:
                continue
            parsed = _parse_sensor_line(raw.decode("utf-8", errors="ignore"))
            if parsed:
                angle, distance = parsed
                if 0 <= angle <= 180 and 0 < distance <= 1000:
                    with serial_lock:
                        serial_state["last_reading"] = {
                            "angle": round(angle, 2),
                            "distance": round(distance, 2),
                            "received_at": _serial_time.time(),
                            "source": "arduino_usb"
                        }
        except Exception as e:
            with serial_lock:
                serial_state["last_error"] = str(e)
                serial_state["connected"] = False
            break
    try:
        if serial_conn:
            serial_conn.close()
    except Exception:
        pass
    serial_conn = None
    serial_state["connected"] = False

def disconnect_arduino():
    global serial_conn
    serial_stop.set()
    try:
        if serial_conn:
            serial_conn.close()
    except Exception:
        pass
    serial_conn = None
    serial_state["connected"] = False
    serial_state["port"] = None

def connect_arduino(port, baud=115200):
    global serial_conn, serial_thread
    if serial is None:
        return {"ok": False, "error": "pyserial is not installed. Run: python -m pip install pyserial"}
    disconnect_arduino()
    try:
        serial_conn = serial.Serial(port, int(baud), timeout=1)
        _serial_time.sleep(2.0)
        serial_stop.clear()
        serial_thread = threading.Thread(target=_serial_reader, daemon=True)
        serial_thread.start()
        serial_state.update({"connected": True, "port": port,
                             "baud": int(baud), "last_error": None})
        return {"ok": True, "port": port, "baud": int(baud)}
    except Exception as e:
        serial_conn = None
        serial_state.update({"connected": False, "port": None,
                             "last_error": str(e)})
        return {"ok": False, "error": str(e)}

def get_serial_reading():
    with serial_lock:
        return dict(serial_state["last_reading"]) if serial_state["last_reading"] else None



app.secret_key = "radar_demo_secret_key_2026"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(
    BASE_DIR,
    "radar_data.csv"
)


# =========================================================
# RADAR STATE
# =========================================================

radar_data = []

current_index = 0

running = False


# =========================================================
# GENERATE SIMULATED RADAR DATA
# =========================================================

def generate_data(num_scans=10):

    data = []

    # Simulated objects
    objects = {
        30: 55,
        75: 90,
        120: 45,
        155: 70
    }

    for scan in range(num_scans):

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        for angle in range(0, 181, 5):

            # Normal background distance
            distance = np.random.uniform(
                150,
                300
            )

            # Simulated objects
            for object_angle, object_distance in objects.items():

                if abs(angle - object_angle) <= 5:

                    distance = (
                        object_distance
                        + np.random.normal(0, 4)
                    )

            # Sensor noise
            noise = np.random.normal(0, 2)

            raw_distance = distance + noise

            # Artificial abnormal readings
            if np.random.random() < 0.02:

                raw_distance = np.random.choice(
                    [0, 450]
                )

            data.append({
                "timestamp": timestamp,
                "scan": scan + 1,
                "angle": angle,
                "raw_distance": round(
                    raw_distance,
                    2
                )
            })

    df = pd.DataFrame(data)

    df.to_csv(
        DATA_FILE,
        index=False
    )

    return df


# =========================================================
# LOAD + CLEAN DATA
# =========================================================

def load_data():

    global radar_data

    if not os.path.exists(DATA_FILE):

        generate_data()

    df = pd.read_csv(DATA_FILE)

    # Convert numeric columns
    df["angle"] = pd.to_numeric(
        df["angle"],
        errors="coerce"
    )

    df["raw_distance"] = pd.to_numeric(
        df["raw_distance"],
        errors="coerce"
    )

    # Remove missing values
    df = df.dropna(
        subset=[
            "angle",
            "raw_distance"
        ]
    ).copy()

    # Remove abnormal sensor readings
    df = df[
        (df["raw_distance"] >= 2)
        &
        (df["raw_distance"] <= 400)
    ].copy()

    # -----------------------------------------------------
    # Median smoothing
    # -----------------------------------------------------

    if "scan" in df.columns:

        df["distance"] = (
            df
            .groupby("scan")["raw_distance"]
            .transform(
                lambda x:
                x.rolling(
                    window=5,
                    center=True,
                    min_periods=1
                ).median()
            )
        )

    else:

        df["distance"] = (
            df["raw_distance"]
            .rolling(
                window=5,
                center=True,
                min_periods=1
            )
            .median()
        )

    # Fill remaining values
    df["distance"] = (
        df["distance"]
        .bfill()
        .ffill()
    )

    radar_data = df.to_dict(
        "records"
    )


# =========================================================
# STATISTICS
# =========================================================

def get_statistics():

    if not radar_data:

        load_data()

    df = pd.DataFrame(
        radar_data
    )

    if df.empty:

        return {
            "total": 0,
            "detected": 0,
            "detection_rate": 0,
            "average": 0,
            "minimum": 0,
            "maximum": 0,
            "median": 0
        }

    detected = df[
        df["distance"] < 100
    ]

    total = len(df)

    detected_count = len(
        detected
    )

    detection_rate = (
        detected_count / total
    ) * 100 if total > 0 else 0

    return {

        "total": total,

        "detected":
            detected_count,

        "detection_rate":
            round(
                detection_rate,
                2
            ),

        "average":
            round(
                df["distance"].mean(),
                2
            ),

        "minimum":
            round(
                df["distance"].min(),
                2
            ),

        "maximum":
            round(
                df["distance"].max(),
                2
            ),

        "median":
            round(
                df["distance"].median(),
                2
            )
    }


# =========================================================
# HOME
# =========================================================


@app.route("/api/serial/ports")
def api_serial_ports():
    return jsonify({"ports": list_serial_ports(), "pyserial": serial is not None,
                    "connected": serial_state["connected"], "port": serial_state["port"],
                    "last_error": serial_state["last_error"]})

@app.route("/api/serial/connect", methods=["POST"])
def api_serial_connect():
    payload = request.get_json(silent=True) or {}
    return jsonify(connect_arduino(payload.get("port"), payload.get("baud", 115200)))

@app.route("/api/serial/disconnect", methods=["POST"])
def api_serial_disconnect():
    disconnect_arduino()
    return jsonify({"ok": True})

@app.route("/api/serial/auto", methods=["POST"])
def api_serial_auto():
    ports = list_serial_ports()
    if not ports:
        return jsonify({"ok": False, "error": "No serial port detected."})
    if len(ports) > 1:
        return jsonify({"ok": False, "multiple": True, "ports": ports,
                        "error": "Multiple serial ports detected. Select the Arduino COM port."})
    return jsonify(connect_arduino(ports[0]["port"], 115200))

@app.route("/api/serial/status")
def api_serial_status():
    return jsonify({"connected": serial_state["connected"], "port": serial_state["port"],
                    "baud": serial_state["baud"], "last_reading": get_serial_reading(),
                    "last_error": serial_state["last_error"]})

@app.route("/api/protocol")
def api_protocol():
    return jsonify({
        "name": "Smart Radar Plug-and-Play Protocol",
        "version": "1.0",
        "transport": "USB Serial",
        "baud_rates": [115200, 9600],
        "csv_format": "angle,distance",
        "csv_example": "75,42",
        "json_format": {"angle": 75, "distance": 42},
        "angle_range": [0, 180],
        "distance_unit": "cm"
    })

@app.route("/api/sensor/input", methods=["POST"])
def api_sensor_input():
    payload = request.get_json(silent=True) or {}
    try:
        angle = float(payload.get("angle"))
        distance = float(payload.get("distance"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "angle and distance are required numeric values"}), 400
    if not (0 <= angle <= 180):
        return jsonify({"ok": False, "error": "angle must be between 0 and 180 degrees"}), 400
    if not (0 < distance <= 1000):
        return jsonify({"ok": False, "error": "distance must be between 0 and 1000 cm"}), 400
    reading = {
        "angle": round(angle, 2),
        "distance": round(distance, 2),
        "raw_distance": round(distance, 2),
        "received_at": _serial_time.time(),
        "source": str(payload.get("source") or "api")
    }
    with serial_lock:
        serial_state["last_reading"] = reading
    # Keep a bounded live history so Analytics can use real browser/network readings.
    global radar_data
    radar_data.append(reading.copy())
    if len(radar_data) > 5000:
        del radar_data[:-5000]
    response = jsonify({"ok": True, "reading": reading})
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

@app.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "service": "Smart Radar Online Platform",
        "browser_usb": True,
        "protocol": "angle,distance",
        "version": "2.0"
    })


@app.route("/")
def home():

    if not session.get(
        "logged_in"
    ):

        return redirect(
            url_for("login")
        )

    return render_template_string(
        HTML
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# =========================================================
# GENERATE NEW DATA
# =========================================================

@app.route("/api/generate")
def api_generate():

    global current_index

    generate_data()

    load_data()

    current_index = 0

    return jsonify({

        "success": True,

        "message":
            "New radar data generated",

        "statistics":
            get_statistics()
    })


# =========================================================
# START RADAR
# =========================================================

@app.route("/api/start")
def api_start():

    global running

    running = True

    return jsonify({

        "running": True
    })


# =========================================================
# STOP RADAR
# =========================================================

@app.route("/api/stop")
def api_stop():

    global running

    running = False

    return jsonify({

        "running": False
    })


# =========================================================
# NEXT RADAR READING
# =========================================================

@app.route("/api/next")
def api_next():

    global current_index

    if not radar_data:

        load_data()

    if current_index >= len(
        radar_data
    ):

        current_index = 0

    row = radar_data[
        current_index
    ]

    angle = float(
        row["angle"]
    )

    distance = float(
        row["distance"]
    )

    # Read the current safety thresholds from the web app settings.
    try:
        danger = float(request.args.get("danger", 60))
        warning = float(request.args.get("warning", 120))
    except (TypeError, ValueError):
        danger, warning = 60.0, 120.0

    danger = max(1.0, danger)
    warning = max(danger + 1.0, warning)

    # An object is considered detected whenever it enters the warning zone.
    detected = distance < warning

    # Threat classification
    if distance < danger:
        threat = "HIGH"
    elif distance < warning:
        threat = "MEDIUM"
    else:
        threat = "LOW"

    # Object status
    if detected:

        object_status = (
            "OBJECT DETECTED"
        )

    else:

        object_status = "CLEAR"

    current_index += 1

    return jsonify({

        "angle":
            angle,

        "distance":
            round(
                distance,
                2
            ),

        "detected":
            detected,

        "threat":
            threat,

        "status":
            object_status
    })


# =========================================================
# STATISTICS API
# =========================================================

@app.route("/api/stats")
def api_stats():

    return jsonify(
        get_statistics()
    )


@app.route("/api/analytics")
def api_analytics():
    """Return dashboard analytics using the currently loaded radar dataset."""
    if not radar_data:
        load_data()

    df = pd.DataFrame(radar_data)
    if df.empty:
        return jsonify({
            "total": 0, "detected": 0, "detection_rate": 0,
            "average": 0, "minimum": 0, "maximum": 0, "median": 0,
            "high": 0, "warning": 0, "safe": 0, "peak_angle": 0
        })

    try:
        danger = max(1.0, float(request.args.get("danger", 60)))
        warning = max(danger + 1.0, float(request.args.get("warning", 120)))
    except (TypeError, ValueError):
        danger, warning = 60.0, 120.0

    distances = pd.to_numeric(df["distance"], errors="coerce").dropna()
    angles = pd.to_numeric(df["angle"], errors="coerce")
    total = len(distances)
    high = int((distances < danger).sum())
    warning_count = int(((distances >= danger) & (distances < warning)).sum())
    safe = int((distances >= warning).sum())
    detected = high + warning_count

    # Peak angle = angle having the closest observed object.
    valid = df.copy()
    valid["distance"] = pd.to_numeric(valid["distance"], errors="coerce")
    valid["angle"] = pd.to_numeric(valid["angle"], errors="coerce")
    valid = valid.dropna(subset=["distance", "angle"])
    peak_angle = float(valid.loc[valid["distance"].idxmin(), "angle"]) if not valid.empty else 0

    return jsonify({
        "total": int(total),
        "detected": int(detected),
        "detection_rate": round((detected / total) * 100, 2) if total else 0,
        "average": round(float(distances.mean()), 2) if total else 0,
        "minimum": round(float(distances.min()), 2) if total else 0,
        "maximum": round(float(distances.max()), 2) if total else 0,
        "median": round(float(distances.median()), 2) if total else 0,
        "high": high,
        "warning": warning_count,
        "safe": safe,
        "peak_angle": round(peak_angle, 2),
        "danger_threshold": danger,
        "warning_threshold": warning
    })


# =========================================================
# CSV UPLOAD
# =========================================================

@app.route(
    "/api/upload",
    methods=["POST"]
)
def upload_data():

    global current_index

    if "file" not in request.files:

        return jsonify({

            "success": False,

            "message":
                "No file uploaded"

        }), 400

    file = request.files[
        "file"
    ]

    if file.filename == "":

        return jsonify({

            "success": False,

            "message":
                "Please select a CSV file"

        }), 400

    if not file.filename.lower().endswith(
        ".csv"
    ):

        return jsonify({

            "success": False,

            "message":
                "Only CSV files are supported"

        }), 400

    try:

        # Read CSV
        df = pd.read_csv(
            file
        )

        # Required columns
        required_columns = [
            "angle",
            "raw_distance"
        ]

        missing = [
            col
            for col in required_columns
            if col not in df.columns
        ]

        if missing:

            return jsonify({

                "success": False,

                "message":
                    "Missing columns: "
                    +
                    ", ".join(missing)

            }), 400

        # Convert numeric data
        df["angle"] = pd.to_numeric(
            df["angle"],
            errors="coerce"
        )

        df["raw_distance"] = pd.to_numeric(
            df["raw_distance"],
            errors="coerce"
        )

        before = len(df)

        # Remove missing values
        df = df.dropna(
            subset=[
                "angle",
                "raw_distance"
            ]
        ).copy()

        # Detect abnormal readings
        invalid = (
            (df["raw_distance"] < 2)
            |
            (df["raw_distance"] > 400)
        )

        outliers = int(
            invalid.sum()
        )

        # Remove outliers
        df = df[
            ~invalid
        ].copy()

        # Median smoothing
        df["distance"] = (
            df["raw_distance"]
            .rolling(
                window=5,
                center=True,
                min_periods=1
            )
            .median()
            .bfill()
            .ffill()
        )

        # Save processed dataset
        df.to_csv(
            DATA_FILE,
            index=False
        )

        # Reload application data
        load_data()

        current_index = 0

        return jsonify({

            "success": True,

            "message":
                "CSV uploaded and processed successfully",

            "total":
                before,

            "valid":
                len(df),

            "outliers":
                outliers,

            "statistics":
                get_statistics()
        })

    except Exception as e:

        return jsonify({

            "success": False,

            "message":
                str(e)

        }), 400


# =========================================================
# EXPORT CSV
# =========================================================

@app.route("/api/export")
def export_data():

    if not os.path.exists(
        DATA_FILE
    ):

        generate_data()

    return send_file(

        DATA_FILE,

        as_attachment=True,

        download_name=
            "radar_processed_data.csv"
    )


# =========================================================
# CHART DATA
# =========================================================

@app.route("/api/chart")
def api_chart():

    if not radar_data:

        load_data()

    df = pd.DataFrame(
        radar_data
    )

    chart_df = (
        df
        .groupby("angle")[
            [
                "raw_distance",
                "distance"
            ]
        ]
        .mean()
        .reset_index()
    )

    return jsonify({

        "angle":
            chart_df[
                "angle"
            ].tolist(),

        "raw":
            chart_df[
                "raw_distance"
            ]
            .round(2)
            .tolist(),

        "cleaned":
            chart_df[
                "distance"
            ]
            .round(2)
            .tolist()
    })


# =========================================================
# LOGIN PAGE
# =========================================================

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart Radar Platform</title>
<style>
:root{--bg:#05070a;--panel:#0b1016;--panel2:#101720;--line:#1d2933;--text:#e8f0f5;--muted:#7e8c98;--green:#39ff88;--cyan:#25d9ff;--yellow:#ffc857;--red:#ff4057;}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:radial-gradient(circle at 65% 20%,#102018 0,#070b0f 35%,#040608 100%);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}button,input{font:inherit}.app{display:flex;min-height:100vh}.sidebar{width:230px;background:rgba(6,10,14,.96);border-right:1px solid var(--line);padding:22px 14px;position:fixed;left:0;top:0;bottom:0;z-index:5}.brand{padding:4px 10px 22px;border-bottom:1px solid var(--line);margin-bottom:18px}.brand h1{font-size:16px;letter-spacing:1.8px;margin:0;color:var(--green)}.brand p{font-size:10px;color:var(--muted);margin:7px 0 0;letter-spacing:1.3px}.nav{display:grid;gap:7px}.nav button,.nav a{border:1px solid transparent;background:transparent;color:#9aa7b1;text-align:left;padding:12px 13px;border-radius:9px;cursor:pointer;font-weight:600;text-decoration:none;display:block}.nav button:hover,.nav button.active,.nav a:hover,.nav a.active{background:#101a20;border-color:#22323c;color:#fff}.nav button.active,.nav a.active{box-shadow:inset 3px 0 var(--green);color:var(--green)}.main{margin-left:230px;width:calc(100% - 230px);min-width:0}.topbar{height:72px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 28px;background:rgba(4,8,11,.82);backdrop-filter:blur(10px);position:sticky;top:0;z-index:4}.title{font-size:20px;font-weight:800;letter-spacing:2px}.online{display:flex;align-items:center;gap:8px;color:#cfe8d8;font-size:12px;font-weight:700}.dot{width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}.content{padding:24px;max-width:1500px;margin:auto}.page{display:none}.page.active{display:block}.hero{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}.hero h2{margin:0;font-size:27px}.hero p{margin:6px 0 0;color:var(--muted)}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid #263640;background:#0c141a;color:#dce8ee;padding:10px 14px;border-radius:8px;cursor:pointer}.btn:hover{border-color:#4b6878;transform:translateY(-1px)}.btn.primary{border-color:#1d9c61;color:var(--green);background:#0b1913}.btn.danger{border-color:#74303a;color:#ff8290;background:#1a0c10}.radar-layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(280px,.65fr);gap:18px}.card{background:linear-gradient(145deg,rgba(14,21,27,.97),rgba(7,12,16,.97));border:1px solid var(--line);border-radius:14px;box-shadow:0 14px 45px rgba(0,0,0,.25)}.radar-card{padding:14px;min-height:560px}.radar-head{display:flex;justify-content:space-between;align-items:center;padding:3px 4px 12px}.eyebrow{font-size:11px;letter-spacing:1.6px;color:var(--muted)}.radar-state{font-size:11px;color:var(--green);letter-spacing:1px}.radar-wrap{position:relative;width:100%;height:490px;display:flex;align-items:center;justify-content:center;background:#030708;border:1px solid #14221e;border-radius:10px;overflow:hidden}.radar-wrap canvas{width:100%;height:100%;display:block}.target-panel{padding:15px;display:grid;gap:12px}.target-title{font-size:12px;color:var(--muted);letter-spacing:1.5px}.target-main{border:1px solid #24323a;border-radius:12px;padding:20px;text-align:center;background:rgba(7,13,17,.8)}.target-main .value{font-size:45px;font-weight:800;letter-spacing:-2px}.unit{font-size:13px;color:var(--muted);margin-left:4px}.threat{display:inline-flex;padding:7px 14px;border-radius:20px;font-size:12px;font-weight:800;margin-top:8px;border:1px solid #35424a}.threat.high{color:#ff7382;border-color:#71313b;background:#260e13}.threat.medium{color:#ffd36e;border-color:#69572b;background:#211b0b}.threat.low{color:var(--green);border-color:#286243;background:#0d2117}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{border:1px solid #1e2b33;border-radius:10px;padding:13px;background:#080e13}.metric span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1.2px}.metric strong{display:block;font-size:19px;margin-top:6px}.status-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:18px}.status-card{padding:15px;border:1px solid var(--line);border-radius:11px;background:#090f14}.status-card span{display:block;color:var(--muted);font-size:10px;letter-spacing:1px;text-transform:uppercase}.status-card strong{display:block;margin-top:6px;font-size:18px}.section-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.feature-card{padding:20px;min-height:150px}.feature-card h3{margin:0 0 8px}.feature-card p{color:var(--muted);line-height:1.55;font-size:13px}.zone{height:16px;border-radius:20px;margin:12px 0;background:linear-gradient(90deg,var(--red) 0 15%,var(--yellow) 15% 30%,var(--green) 30% 100%);border:1px solid #1d2933;transition:.25s}.zone-labels{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}.alert-list{display:grid;gap:10px;margin-top:15px}.alert-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:4px}.alert-stat{padding:16px}.alert-stat span{display:block;color:var(--muted);font-size:10px;letter-spacing:1px}.alert-stat strong{display:block;font-size:27px;margin-top:7px}.alert-monitor{margin-top:15px;padding:17px;display:flex;align-items:center;justify-content:space-between;gap:15px}.alert-monitor h3{margin:7px 0 4px}.alert-monitor p{margin:0;color:var(--muted);font-size:12px}.alert{transition:.2s}.alert:hover{transform:translateY(-1px);border-color:#34454f}.alert-meta{color:var(--muted);font-size:11px;margin-top:4px}.alert-filter-active{border-color:var(--green)!important;color:var(--green)!important}@media(max-width:900px){.alert-summary{grid-template-columns:1fr 1fr}}@media(max-width:560px){.alert-summary{grid-template-columns:1fr}.alert-monitor{display:block}.alert-monitor .threat{margin-top:12px}}.alert{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center;padding:14px;border-radius:10px;border:1px solid #202c34;background:#090f14}.alert.high{border-left:4px solid var(--red)}.alert.medium{border-left:4px solid var(--yellow)}.badge{padding:5px 9px;border-radius:6px;font-size:10px;font-weight:800}.badge.high{background:#2b0e13;color:#ff7382}.badge.medium{background:#2a210d;color:#ffd36e}.empty{padding:35px;text-align:center;color:var(--muted);border:1px dashed #26333b;border-radius:10px}.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.bigstat{padding:18px}.bigstat span{color:var(--muted);font-size:11px}.bigstat strong{display:block;font-size:29px;margin-top:8px}.charts{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-top:15px} .analytics-extra{margin-top:12px}.analytics-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:12px;padding:16px}.analytics-summary div{background:#080e13;border:1px solid var(--line);border-radius:12px;padding:14px}.analytics-summary span{display:block;color:var(--muted);font-size:10px;letter-spacing:1px}.analytics-summary strong{display:block;margin-top:7px;font-size:20px}.threat-chart-card{grid-column:1/-1}@media(max-width:900px){.analytics-summary{grid-template-columns:repeat(2,1fr)}.charts{grid-template-columns:1fr}}.chart-card{padding:16px}.chart-card h3{font-size:13px;margin:0 0 10px}.chart-card canvas{width:100%;height:260px;background:#060a0d;border-radius:8px}.settings-grid .form-card select{background:#081016;color:var(--text);border:1px solid #263642;border-radius:8px;padding:10px 12px}.serial-live{color:var(--green)!important}.settings-status{margin-bottom:15px;padding:17px;display:flex;align-items:center;justify-content:space-between;gap:15px}.settings-status h3{margin:7px 0 4px}.settings-status p{margin:0;color:var(--muted);font-size:12px}.settings-pill{border:1px solid #254235;border-radius:999px;padding:8px 12px;font-size:10px;letter-spacing:1px;color:var(--green);white-space:nowrap}.settings-help{margin-top:15px;padding:11px 12px;border:1px solid #1d2933;border-radius:10px;color:var(--muted);font-size:11px;line-height:1.5}@media(max-width:700px){.settings-status{display:block}.settings-pill{display:inline-block;margin-top:12px}}.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.form-card{padding:20px}.form-row{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:15px 0;border-bottom:1px solid #1a252c}.form-row:last-child{border-bottom:0}.form-row label{font-size:13px}.form-row small{display:block;color:var(--muted);margin-top:4px}.form-row input{width:110px;background:#070d12;border:1px solid #27353e;color:#fff;border-radius:7px;padding:9px}.connect-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:15px}.connect-card{padding:22px}.connect-options{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:15px}.connect-option{border:1px solid #24323a;background:#080e13;border-radius:12px;padding:16px;cursor:pointer;text-align:left;color:var(--text)}.connect-option:hover,.connect-option.active{border-color:var(--green);background:#0b1913}.connect-option strong{display:block;margin-top:7px}.connect-option small{display:block;color:var(--muted);margin-top:5px;line-height:1.4}.connect-step{display:flex;gap:12px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #1a252c}.connect-step:last-child{border-bottom:0}.step-no{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;border:1px solid #2a3942;color:var(--green);font-weight:800}.protocol-box{margin-top:14px;background:#05090c;border:1px solid #1c2a32;border-radius:10px;padding:14px;font-family:Consolas,monospace;font-size:12px;color:#b9c9d2}.connection-big{padding:18px;border:1px solid #24323a;border-radius:12px;background:#080e13}.connection-big .state{font-size:24px;font-weight:800;margin-top:6px}.source-pill{display:inline-block;margin-top:9px;padding:6px 10px;border-radius:999px;border:1px solid #254235;color:var(--green);font-size:10px;letter-spacing:1px}@media(max-width:900px){.connect-grid{grid-template-columns:1fr}.connect-options{grid-template-columns:1fr}}
.toast{position:fixed;right:24px;bottom:24px;background:#101a20;border:1px solid #2b3b44;padding:12px 16px;border-radius:9px;opacity:0;transform:translateY(10px);transition:.25s;z-index:20}.toast.show{opacity:1;transform:none}@media(max-width:900px){.sidebar{width:76px;padding:15px 8px}.brand h1{font-size:10px;letter-spacing:1px}.brand p{display:none}.nav button,.nav a{font-size:0;text-align:center;padding:13px}.nav button::first-letter{font-size:19px}.main{margin-left:76px;width:calc(100% - 76px)}.radar-layout,.settings-grid,.charts{grid-template-columns:1fr}.status-strip,.stats-grid{grid-template-columns:1fr 1fr}.topbar{padding:0 15px}.title{font-size:15px}.content{padding:15px}.radar-wrap{height:410px}}@media(max-width:560px){.status-strip,.stats-grid,.section-grid{grid-template-columns:1fr}.topbar{height:62px}.online{font-size:0}.radar-wrap{height:330px}.radar-card{min-height:0}.hero{display:block}.actions{margin-top:12px}}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
  <div class="brand"><h1>SMART ULTRASONIC RADAR</h1><p>MONITORING SYSTEM</p></div>
  <nav class="nav">
    <a class="active" href="#home" data-page="home">🏠 HOME</a>
    <a href="#connect" data-page="connect">🔌 CONNECT</a>
    <a href="#radarPage" data-page="radarPage">📡 RADAR</a>
    <a href="#safety" data-page="safety">🛡 SAFETY</a>
    <a href="#alerts" data-page="alerts">🔔 ALERTS</a>
    <a href="#analytics" data-page="analytics">📊 ANALYTICS</a>
    <a href="#settings" data-page="settings">⚙ SETTINGS</a>
  </nav>
</aside>
<main class="main">
<header class="topbar"><div class="title">SMART ULTRASONIC RADAR</div><div class="online"><span class="dot" id="onlineDot"></span><span id="systemText">SYSTEM ONLINE</span></div></header>
<div class="content">
<section id="home" class="page active">
  <div class="hero"><div><h2>Monitoring Overview</h2><p>Real-time ultrasonic object monitoring and risk status.</p></div><div class="actions"><button class="btn primary" onclick="generateData()">Generate Data</button><button class="btn" onclick="document.getElementById('csvFile').click()">Upload CSV</button><input id="csvFile" type="file" accept=".csv" hidden onchange="uploadCSV()"></div></div>
  <div class="radar-layout">
    <div class="card radar-card"><div class="radar-head"><div class="eyebrow">LIVE RADAR</div><div class="radar-state" id="radarState">● STANDBY</div></div><div class="radar-wrap"><canvas id="radar" width="900" height="560"></canvas></div><div style="display:flex;gap:14px;flex-wrap:wrap;margin:9px 4px 0;color:#879a91;font-size:10px;letter-spacing:.7px"><span><b style="color:#39ff88">●</b> SAFE</span><span><b style="color:#ffc857">●</b> WARNING</span><span><b style="color:#ff4057">●</b> DANGER</span><span>● PERSISTENT TARGET TRACKING</span></div></div>
    <div class="card target-panel"><div class="target-title">CURRENT TARGET</div><div class="target-main"><div class="value" id="distance">--<span class="unit">cm</span></div><div id="threat" class="threat low">LOW</div></div><div class="metric-grid"><div class="metric"><span>Angle</span><strong id="angle">--°</strong></div><div class="metric"><span>Object</span><strong id="object">CLEAR</strong></div><div class="metric"><span>Status</span><strong id="status">READY</strong></div><div class="metric"><span>Range</span><strong id="rangeLabel">400 cm</strong></div></div><div class="actions"><button class="btn primary" onclick="startRadar()">▶ Start Radar</button><button class="btn danger" onclick="stopRadar()">■ Stop</button></div></div>
  </div>
  <div class="status-strip"><div class="status-card"><span>Total Readings</span><strong id="total">--</strong></div><div class="status-card"><span>Detected</span><strong id="detected">--</strong></div><div class="status-card"><span>Detection Rate</span><strong id="rate">--%</strong></div><div class="status-card"><span>Average Distance</span><strong id="average">-- cm</strong></div></div>
</section>
<section id="connect" class="page">
  <div class="hero"><div><h2>Connect Your Radar</h2><p>Choose <b>Demo</b> for your presentation, or connect a compatible Arduino by USB for live sensor data.</p></div><div class="actions"><button type="button" class="btn primary" onclick="startSimulation()">▶ Start Demo</button><button type="button" class="btn" onclick="selectConnection('browser');connectBrowserUSB()">🔌 Connect Arduino</button></div></div>
  <div class="card feature-card" style="margin-bottom:15px;border-color:#1d9c61;background:linear-gradient(135deg,rgba(11,25,19,.95),rgba(7,12,16,.97))">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap">
      <div><div class="eyebrow">NO SOFTWARE INSTALLATION</div><h3 style="margin:7px 0 5px">Connect → Start Radar → Monitor</h3><p style="margin:0;color:var(--muted);font-size:12px">The website handles visualization, safety classification, alerts and analytics. The student's hardware only needs to send angle + distance.</p></div>
      <div class="settings-pill">ONLINE READY</div>
    </div>
  </div>
  <div class="connect-grid">
    <div class="card connect-card">
      <span class="eyebrow">STEP 1 · CHOOSE SOURCE</span>
      <div class="connect-options">
        <button type="button" class="connect-option active" id="optSimulation" onclick="selectConnection('simulation')"><span style="font-size:24px">🧪</span><strong>Demo Mode</strong><small>No hardware needed. Run a realistic radar demonstration for class or testing.</small></button>
        <button type="button" class="connect-option" id="optBrowser" onclick="selectConnection('browser')"><span style="font-size:24px">🔌</span><strong>Connect Arduino</strong><small>Plug Arduino into USB and connect directly from Chrome/Edge.</small></button>
      </div>
      <div id="simulationSetup" style="margin-top:20px">
        <div class="connection-big" style="border-color:#1d9c61;background:linear-gradient(135deg,rgba(11,25,19,.95),rgba(7,12,16,.97))">
          <div class="eyebrow">NO HARDWARE REQUIRED</div>
          <div class="state">🧪 Simulation Mode</div>
          <p style="color:var(--muted);font-size:12px;line-height:1.55;margin:8px 0 14px">The website generates virtual ultrasonic sensor readings. Use this mode for development, testing, demonstration and college presentation.</p>
          <div class="actions"><button type="button" class="btn primary" onclick="startSimulation()">▶ START DEMO</button><button type="button" class="btn danger" onclick="stopRadar()">■ STOP</button></div>
        </div>
        <div class="connect-step"><div class="step-no">1</div><div><b>Start Simulation</b><div style="color:var(--muted);font-size:12px;margin-top:4px">The system creates angle + distance readings just like an ultrasonic radar sensor.</div></div></div>
        <div class="connect-step"><div class="step-no">2</div><div><b>Monitor the virtual objects</b><div style="color:var(--muted);font-size:12px;margin-top:4px">Radar, Safety Zone and Alerts update automatically from the simulated readings.</div></div></div>
        <div class="connect-step"><div class="step-no">3</div><div><b>Analyze the data</b><div style="color:var(--muted);font-size:12px;margin-top:4px">Analytics uses the generated readings so students can test the complete workflow without hardware.</div></div></div>
      </div>
      <div id="usbSetup" style="display:none;margin-top:20px">
        <div class="connect-step"><div class="step-no">1</div><div><b>Connect the hardware by USB</b><div style="color:var(--muted);font-size:12px;margin-top:4px">Open the online site in Chrome/Edge and click Browser USB. The browser asks permission to use the selected serial device.</div></div></div>
        <div class="connect-step"><div class="step-no">2</div><div><b>Use the standard sensor protocol</b><div style="color:var(--muted);font-size:12px;margin-top:4px">The hardware sends angle and distance, for example <b>75,42</b>. The website converts it into radar data.</div></div></div>
        <div class="connect-step"><div class="step-no">3</div><div><b>Start monitoring</b><div style="color:var(--muted);font-size:12px;margin-top:4px">The same live reading feeds Radar, Safety Zone, Alerts and Analytics automatically.</div></div></div>
        <div class="protocol-box">Supported: 75,42<br>{"angle":75,"distance":42}<br>angle=75,distance=42</div>
        <div class="actions" style="margin-top:15px"><button class="btn primary" onclick="connectBrowserUSB()">🌐 Connect Browser USB</button><button class="btn" onclick="refreshSerialPorts()">↻ Local COM Scan</button><button class="btn" onclick="page('radarPage');startRadar()">▶ Demo Mode</button></div>
      </div>
      <div id="networkSetup" style="display:none!important;margin-top:20px">
        <div class="connect-step"><div class="step-no">1</div><div><b>Send sensor data</b><div style="color:var(--muted);font-size:12px;margin-top:4px">POST readings to <code>/api/sensor/input</code>.</div></div></div>
        <div class="protocol-box">POST /api/sensor/input<br>Content-Type: application/json<br><br>{"angle":75,"distance":42,"source":"esp32"}</div>
        <div class="actions" style="margin-top:15px"><button class="btn primary" onclick="page('radarPage');startRadar()">▶ Open Live Radar</button></div>
      </div>
    </div>
    <div class="card connect-card">
      <span class="eyebrow">CONNECTION STATUS</span>
      <div class="connection-big" style="margin-top:12px"><div class="eyebrow">CURRENT SOURCE</div><div class="state" id="connectState">SIMULATION MODE</div><span class="source-pill" id="connectSource">DEMO / SIMULATION</span></div>
      <div class="metric-grid" style="margin-top:12px"><div class="metric"><span>Angle</span><strong id="connectAngle">--°</strong></div><div class="metric"><span>Distance</span><strong id="connectDistance">-- cm</strong></div><div class="metric"><span>Threat</span><strong id="connectThreat">LOW</strong></div><div class="metric"><span>Protocol</span><strong>Universal</strong></div></div>
      <div class="settings-help" style="margin-top:12px"><b id="browserUsbSupport">Checking Browser USB support…</b><br><br><b>Plug & Play rule:</b> compatible hardware only needs to provide angle + distance. Your application performs visualization, threat classification, alerts and analytics.</div>
    </div>
  </div>
</section>
<section id="radarPage" class="page"><div class="hero"><div><h2>Live Radar</h2><p>Monitor angle, distance and threat level in real time.</p></div><div class="actions"><button class="btn primary" onclick="startRadar()">▶ Start</button><button class="btn danger" onclick="stopRadar()">■ Stop</button></div></div><div class="card radar-card"><div class="radar-wrap" style="height:min(68vh,650px)"><canvas id="radar2" width="1000" height="650"></canvas></div><div style="display:flex;gap:14px;flex-wrap:wrap;margin:9px 4px 0;color:#879a91;font-size:10px;letter-spacing:.7px"><span><b style="color:#39ff88">●</b> SAFE</span><span><b style="color:#ffc857">●</b> WARNING</span><span><b style="color:#ff4057">●</b> DANGER</span><span>● PERSISTENT TARGET TRACKING</span></div></div></section>
<section id="safety" class="page">
  <div class="hero">
    <div><h2>Safety Zone</h2><p>Live distance-based risk classification for detected objects.</p></div>
  </div>
  <div class="card feature-card" style="margin-bottom:15px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <div>
        <div class="eyebrow">CURRENT SAFETY STATUS</div>
        <h2 id="safetyStatus" style="margin:7px 0 3px">SYSTEM READY</h2>
        <p id="safetyDetail" style="margin:0;color:var(--muted)">Start the radar to monitor the current safety zone.</p>
      </div>
      <div id="safetyBadge" class="threat low">SAFE</div>
    </div>
  </div>
  <div class="section-grid">
    <div class="card feature-card"><h3>🟢 Safe Zone</h3><p>Objects at or beyond the warning threshold are safe.</p><strong id="safeThreshold">≥ 120 cm</strong></div>
    <div class="card feature-card"><h3>🟡 Warning Zone</h3><p>Objects inside the warning range require attention.</p><strong id="warningThreshold">60–119 cm</strong></div>
    <div class="card feature-card"><h3>🔴 Danger Zone</h3><p>Close objects are classified as high threat.</p><strong id="dangerThreshold">&lt; 60 cm</strong></div>
  </div>
  <div class="card feature-card" style="margin-top:15px">
    <h3>Safety Distance Map</h3>
    <div class="zone" id="safetyZoneBar"></div>
    <div class="zone-labels"><span>0 cm</span><span id="warningZoneLabel">60–119 cm</span><span id="zoneRangeLabel">SAFE — 400 cm</span></div>
    <div style="margin-top:14px;color:var(--muted);font-size:11px">Danger = red · Warning = yellow · Safe = green · Thresholds follow Settings.</div>
  </div>
</section>
<section id="alerts" class="page">
  <div class="hero">
    <div><h2>Alerts</h2><p>Live warning and high-threat detection history.</p></div>
    <div class="actions">
      <button class="btn" onclick="setAlertFilter('ALL')">All</button>
      <button class="btn" onclick="setAlertFilter('HIGH')">Danger</button>
      <button class="btn" onclick="setAlertFilter('MEDIUM')">Warning</button>
      <button class="btn danger" onclick="clearAlerts()">Clear Alerts</button>
    </div>
  </div>
  <div class="alert-summary">
    <div class="card alert-stat"><span>🔴 DANGER EVENTS</span><strong id="alertHighCount">0</strong></div>
    <div class="card alert-stat"><span>🟡 WARNING EVENTS</span><strong id="alertMediumCount">0</strong></div>
    <div class="card alert-stat"><span>⚠ TOTAL ALERTS</span><strong id="alertTotalCount">0</strong></div>
    <div class="card alert-stat"><span>LAST EVENT</span><strong id="alertLastEvent">--</strong></div>
  </div>
  <div class="card alert-monitor">
    <div>
      <span class="eyebrow">ALERT MONITOR</span>
      <h3 id="alertMonitorTitle">Waiting for a safety event</h3>
      <p id="alertMonitorDetail">Start the radar to receive warning and danger notifications.</p>
    </div>
    <div id="alertMonitorBadge" class="threat low">READY</div>
  </div>
  <div id="alertList" class="alert-list"><div class="empty">No alerts yet. Start the radar to monitor objects.</div></div>
</section>
<section id="analytics" class="page"><div class="hero"><div><h2>Analytics</h2><p>Statistics from the current radar dataset.</p></div><div class="actions"><button class="btn" onclick="analyze()">↻ Refresh</button><button class="btn" onclick="exportCSV()">Export CSV</button></div></div><div class="stats-grid"><div class="card bigstat"><span>TOTAL READINGS</span><strong id="aTotal">--</strong></div><div class="card bigstat"><span>DETECTED</span><strong id="aDetected">--</strong></div><div class="card bigstat"><span>DETECTION RATE</span><strong id="aRate">--%</strong></div><div class="card bigstat"><span>AVERAGE DISTANCE</span><strong id="aAverage">-- cm</strong></div></div><div class="stats-grid analytics-extra"><div class="card bigstat"><span>🔴 HIGH</span><strong id="aHigh">--</strong></div><div class="card bigstat"><span>🟡 WARNING</span><strong id="aWarning">--</strong></div><div class="card bigstat"><span>🟢 SAFE</span><strong id="aSafe">--</strong></div><div class="card bigstat"><span>PEAK ANGLE</span><strong id="aPeakAngle">--°</strong></div></div><div class="card feature-card analytics-summary"><div><span>MIN DISTANCE</span><strong id="aMin">-- cm</strong></div><div><span>MAX DISTANCE</span><strong id="aMax">-- cm</strong></div><div><span>MEDIAN DISTANCE</span><strong id="aMedian">-- cm</strong></div><div><span>THREAT MIX</span><strong id="aThreatMix">--</strong></div></div><div class="charts"><div class="card chart-card"><h3>Distance vs Angle</h3><canvas id="distanceChart" width="700" height="260"></canvas></div><div class="card chart-card"><h3>Raw vs Cleaned Distance</h3><canvas id="angleChart" width="700" height="260"></canvas></div><div class="card chart-card threat-chart-card"><h3>Threat Distribution</h3><canvas id="threatChart" width="700" height="240"></canvas></div></div></section>
<section id="settings" class="page">
  <div class="hero">
    <div><h2>Settings</h2><p>Configure the radar monitoring experience.</p></div>
    <div class="actions">
      <button class="btn" onclick="resetSettings()">Reset Defaults</button>
      <button class="btn primary" onclick="saveSettings()">Save Settings</button>
    </div>
  </div>
  <div class="card settings-status">
    <div>
      <span class="eyebrow">CONFIGURATION STATUS</span>
      <h3 id="settingsStatus">ACTIVE CONFIGURATION</h3>
      <p id="settingsSummary">Range 400 cm · Danger &lt; 60 cm · Warning 60–119 cm · Sweep 120 ms</p>
    </div>
    <div class="settings-pill">LOCAL STORAGE</div>
  </div>
  <div class="settings-grid">
    <div class="card form-card">
      <h3>Radar Configuration</h3>
      <div class="form-row"><div><label>Radar Range</label><small>Maximum monitoring distance</small></div><input id="rangeInput" type="number" min="50" max="1000" value="400"></div>
      <div class="form-row"><div><label>Danger Threshold</label><small>High threat below this distance</small></div><input id="dangerInput" type="number" min="10" max="300" value="60"></div>
      <div class="form-row"><div><label>Warning Threshold</label><small>Warning below this distance</small></div><input id="warningInput" type="number" min="20" max="500" value="120"></div>
      <div class="form-row"><div><label>Sweep Speed</label><small>Delay between sensor readings</small></div><input id="speedInput" type="number" min="20" max="500" value="120"></div>
      <div class="settings-help">Recommended order: <b>Danger &lt; Warning &lt; Range</b>. Changes apply to Radar, Safety Zone and threat classification.</div>
    </div>
    <div class="card form-card">
      <h3>🔌 Arduino USB / Live Sensor</h3>
      <p style="color:var(--muted);line-height:1.6">Connect an Arduino Uno by USB. A compatible student radar project can send angle and distance readings directly to this dashboard — no separate dashboard code is required.</p>
      <div class="form-row"><div><label>USB / COM Port</label><small id="serialPortHelp">Detecting serial ports…</small></div><select id="serialPort"><option value="">Select port</option></select></div>
      <div class="form-row"><div><label>Baud Rate</label><small>Match the Arduino sketch</small></div><select id="serialBaud"><option value="115200">115200</option><option value="9600">9600</option></select></div>
      <div class="actions"><button class="btn primary" onclick="refreshSerialPorts()">↻ Detect Arduino</button><button class="btn" onclick="autoConnectArduino()">⚡ Auto Connect</button><button class="btn" onclick="connectArduino()">Connect USB</button><button class="btn primary" onclick="connectBrowserUSB()">🌐 Browser USB (Online)</button><button class="btn danger" onclick="disconnectArduino()">Disconnect</button></div>
      <div class="settings-status" style="margin-top:14px;margin-bottom:0;padding:12px"><div><span class="eyebrow">SENSOR STATUS</span><div id="serialStatus" style="margin-top:5px;font-weight:700">SIMULATION MODE</div></div><div id="serialBadge" class="settings-pill">SIMULATION</div></div>
      <p style="margin-top:12px;color:var(--muted);font-size:11px">Expected serial format: <b>angle,distance</b> such as <b>75,42</b>, or JSON <b>{"angle":75,"distance":42}</b>.</p><p style="margin-top:8px;color:var(--muted);font-size:11px"><b>Online mode:</b> Chrome/Edge can connect to USB directly from an HTTPS site (or localhost) after user permission.</p>
      <div class="settings-status" style="margin-top:14px;margin-bottom:0;padding:12px">
        <div><span class="eyebrow">PLUG &amp; PLAY</span><div style="margin-top:5px;font-weight:700">USB → Sensor Data → Live Dashboard</div></div>
        <div style="text-align:right;color:var(--muted);font-size:11px;max-width:430px">Any compatible student project only needs to send <b>angle,distance</b>. The app handles radar, safety, alerts and analytics automatically.</div>
      </div>
    </div>
    <div class="card form-card">
      <h3>Data Tools</h3>
      <p style="color:var(--muted);line-height:1.6">Use generated test readings or upload a CSV dataset. The backend processes angle and distance data and provides statistics and charts.</p>
      <div class="actions"><button class="btn primary" onclick="generateData()">Generate New Data</button><button class="btn" onclick="document.getElementById('csvFile').click()">Upload CSV</button><button class="btn" onclick="exportCSV()">Export CSV</button></div>
      <hr style="border-color:#1d2933;margin:22px 0">
      <p style="color:var(--muted);font-size:12px">Settings are stored in this browser using local storage. The architecture is ready for a future live Arduino/ESP32 sensor source.</p>
    </div>
  </div>
</section>
</div></main></div><div id="toast" class="toast"></div>
<script>(() => {
'use strict';

let running=false, timer=null, current={angle:0,distance:0,detected:false,threat:'LOW',status:'READY'}, alerts=[];
let settings={range:400,danger:60,warning:120,speed:120};
let sensorMode='SIMULATION', browserSerialPort=null, browserSerialReader=null, browserSerialRunning=false;
let alertFilter='ALL', lastAlertKey='', sweepAngle=0;
const targetMemory=new Map();
// Show a few realistic demo targets immediately so the dashboard never opens to a blank radar.
[[30,55],[75,90],[120,45],[155,70]].forEach(([angle,distance])=>targetMemory.set(angle,{angle,distance,threat:threatClass(distance),seen:Date.now()}));
const $=id=>document.getElementById(id);

function toast(msg){const t=$('toast');if(!t)return;t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200)}
function safeSettings(){try{const raw=localStorage.getItem('radarSettings');if(raw){const x=JSON.parse(raw);if(x&&Number.isFinite(+x.range)&&Number.isFinite(+x.danger)&&Number.isFinite(+x.warning)&&Number.isFinite(+x.speed))settings={range:+x.range,danger:+x.danger,warning:+x.warning,speed:+x.speed}}}catch(e){settings={range:400,danger:60,warning:120,speed:120}}}
function saveLocal(){try{localStorage.setItem('radarSettings',JSON.stringify(settings))}catch(e){}}

function page(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===name));
  document.querySelectorAll('.nav a,.nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===name));
  if(location.hash!=='#'+name) history.replaceState(null,'','#'+name);
  if(name==='analytics'){analyze();drawCharts()}
  if(name==='alerts')renderAlerts();
  if(name==='connect')updateConnectStatus();
  if(name==='radarPage')setTimeout(()=>drawRadar('radar2'),0);
}
function routeFromHash(){const n=(location.hash||'#home').slice(1);page(document.getElementById(n)?n:'home')}
function bindNavigation(){
  document.querySelectorAll('.nav a,.nav button').forEach(b=>b.addEventListener('click',e=>{const n=b.dataset.page;if(n){e.preventDefault();page(n)}}));
  window.addEventListener('hashchange',routeFromHash);
  routeFromHash();
}

function applySettings(){
  const map=[['rangeInput',settings.range],['dangerInput',settings.danger],['warningInput',settings.warning],['speedInput',settings.speed]];
  map.forEach(([id,v])=>{if($(id))$(id).value=v});
  if($('rangeLabel'))$('rangeLabel').innerText=settings.range+' cm';
  if($('dangerThreshold'))$('dangerThreshold').innerText='< '+settings.danger+' cm';
  if($('warningThreshold'))$('warningThreshold').innerText=settings.danger+'–'+(settings.warning-1)+' cm';
  if($('safeThreshold'))$('safeThreshold').innerText='≥ '+settings.warning+' cm';
  if($('settingsSummary'))$('settingsSummary').innerText=`Range ${settings.range} cm · Danger < ${settings.danger} cm · Warning ${settings.danger}–${settings.warning-1} cm · Sweep ${settings.speed} ms`;
  updateSafetyBar();
}
function saveSettings(){
  const range=+$('rangeInput').value,danger=+$('dangerInput').value,warning=+$('warningInput').value,speed=+$('speedInput').value;
  if(!Number.isFinite(range)||range<50||range>1000)return toast('Range must be 50–1000 cm');
  if(!Number.isFinite(danger)||danger<10||danger>=range)return toast('Danger threshold must be below range');
  if(!Number.isFinite(warning)||warning<=danger||warning>=range)return toast('Warning must be above danger and below range');
  if(!Number.isFinite(speed)||speed<20||speed>500)return toast('Sweep speed must be 20–500 ms');
  settings={range,danger,warning,speed};saveLocal();applySettings();toast('Settings saved');
}
function resetSettings(){settings={range:400,danger:60,warning:120,speed:120};saveLocal();applySettings();toast('Default settings restored')}

function threatClass(d){return d<settings.danger?'HIGH':d<settings.warning?'MEDIUM':'LOW'}
function rememberTarget(d){if(!d||!d.detected)return;const k=Math.round(+d.angle/5)*5;targetMemory.set(k,{angle:+d.angle,distance:+d.distance,threat:threatClass(+d.distance),seen:Date.now()});const now=Date.now();for(const [key,v] of targetMemory)if(now-v.seen>4500)targetMemory.delete(key)}
function drawRadar(id){
  const c=$(id);if(!c)return;const ctx=c.getContext('2d');if(!ctx)return;const w=c.width,h=c.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#030708';ctx.fillRect(0,0,w,h);
  const cx=w/2,cy=h*.88,R=Math.min(w*.45,h*.80);
  ctx.strokeStyle='rgba(57,255,136,.24)';ctx.lineWidth=1;
  for(let i=1;i<=5;i++){ctx.beginPath();ctx.arc(cx,cy,R*i/5,Math.PI,2*Math.PI);ctx.stroke()}
  for(let a=0;a<=180;a+=30){const r=(a-180)*Math.PI/180,ex=cx+Math.cos(r)*R,ey=cy+Math.sin(r)*R;ctx.strokeStyle='rgba(57,255,136,.18)';ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(ex,ey);ctx.stroke();ctx.fillStyle='#6e8379';ctx.font='11px Arial';ctx.fillText(a+'°',cx+Math.cos(r)*(R+18)-9,cy+Math.sin(r)*(R+18)+4)}
  ctx.fillStyle='rgba(154,184,169,.75)';ctx.font='10px Arial';for(let i=1;i<=4;i++)ctx.fillText(Math.round(settings.range*i/5)+' cm',cx+6,cy-R*i/5+12);
  const sr=(sweepAngle-180)*Math.PI/180,sx=cx+Math.cos(sr)*R,sy=cy+Math.sin(sr)*R;ctx.strokeStyle='rgba(57,255,136,.85)';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(sx,sy);ctx.stroke();
  for(const t of targetMemory.values()){const rr=Math.min(R,Math.max(5,t.distance/settings.range*R)),tr=(t.angle-180)*Math.PI/180,tx=cx+Math.cos(tr)*rr,ty=cy+Math.sin(tr)*rr,age=Math.min(1,(Date.now()-t.seen)/4500),alpha=Math.max(.25,1-age),color=t.threat==='HIGH'?'#ff4057':t.threat==='MEDIUM'?'#ffc857':'#39ff88';ctx.save();ctx.globalAlpha=alpha;ctx.shadowBlur=18;ctx.shadowColor=color;ctx.fillStyle=color;ctx.beginPath();ctx.arc(tx,ty,7,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;ctx.strokeStyle=color;ctx.beginPath();ctx.arc(tx,ty,13,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#e8f0f5';ctx.font='bold 11px Arial';ctx.fillText(Math.round(t.distance)+' cm',tx+17,ty-7);ctx.fillStyle=color;ctx.font='10px Arial';ctx.fillText(Math.round(t.angle)+'° · '+t.threat,tx+17,ty+7);ctx.restore()}
  ctx.fillStyle='#39ff88';ctx.shadowBlur=12;ctx.shadowColor='#39ff88';ctx.beginPath();ctx.arc(cx,cy,4,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;
}
function animateRadar(){sweepAngle=(sweepAngle+1.6)%181;drawRadar('radar');drawRadar('radar2');requestAnimationFrame(animateRadar)}

function updateSafetyUI(d){const st=$('safetyStatus'),de=$('safetyDetail'),ba=$('safetyBadge');if(!st||!de||!ba)return;const dist=+d.distance||0,level=d.threat==='HIGH'?'DANGER':d.threat==='MEDIUM'?'WARNING':'SAFE';st.innerText=level;de.innerText=d.detected?`Object detected at ${Math.round(dist)} cm · Angle ${Math.round(d.angle)}°`:`No object inside the warning threshold · Current reading ${Math.round(dist)} cm`;ba.innerText=level;ba.className='threat '+(level==='DANGER'?'high':level==='WARNING'?'medium':'low')}
function updateSafetyBar(){const bar=$('safetyBar'),rl=$('safetyRangeLabel');if(bar){const dangerPct=Math.min(100,settings.danger/settings.range*100),warnPct=Math.min(100,settings.warning/settings.range*100);bar.style.background=`linear-gradient(90deg,var(--red) 0 ${dangerPct}%,var(--yellow) ${dangerPct}% ${warnPct}%,var(--green) ${warnPct}% 100%)`}if(rl)rl.innerText=`0–${settings.range} cm`}
function updateUI(d){if($('distance'))$('distance').innerHTML=Math.round(d.distance)+'<span class="unit">cm</span>';if($('angle'))$('angle').innerText=Math.round(d.angle)+'°';if($('object'))$('object').innerText=d.detected?'DETECTED':'CLEAR';if($('status'))$('status').innerText=d.status||'READY';const th=$('threat');if(th){th.innerText=d.threat;th.className='threat '+(d.threat==='HIGH'?'high':d.threat==='MEDIUM'?'medium':'low')}updateSafetyUI(d);updateConnectStatus()}
function updateConnectStatus(){if(!$('connectState'))return;const live=sensorMode==='LIVE';$('connectState').innerText=live?'LIVE SENSOR':'SIMULATION MODE';$('connectSource').innerText=live?(browserSerialRunning?'ARDUINO USB':'ARDUINO / SERIAL'):'DEMO / SIMULATION';$('connectAngle').innerText=(+current.angle||0).toFixed(0)+'°';$('connectDistance').innerText=(+current.distance||0).toFixed(1)+' cm';$('connectThreat').innerText=current.threat||'LOW'}

function addAlert(d){if(d.threat==='LOW')return;const key=d.threat+'|'+Math.round(d.distance)+'|'+Math.round(d.angle);if(key===lastAlertKey)return;lastAlertKey=key;alerts.unshift({level:d.threat,distance:+d.distance,angle:+d.angle,time:new Date().toLocaleTimeString()});alerts=alerts.slice(0,50);renderAlerts()}
function renderAlerts(){const box=$('alertList');if(!box)return;const high=alerts.filter(a=>a.level==='HIGH').length,med=alerts.filter(a=>a.level==='MEDIUM').length;if($('alertHighCount'))$('alertHighCount').innerText=high;if($('alertMediumCount'))$('alertMediumCount').innerText=med;if($('alertTotalCount'))$('alertTotalCount').innerText=alerts.length;if($('alertLastEvent'))$('alertLastEvent').innerText=alerts.length?alerts[0].time:'--';const visible=alertFilter==='ALL'?alerts:alerts.filter(a=>a.level===alertFilter);if(!visible.length){box.innerHTML='<div class="empty">No alerts yet. Start the radar to monitor objects.</div>';return}box.innerHTML=visible.map(a=>`<div class="alert ${a.level.toLowerCase()}"><span class="badge ${a.level.toLowerCase()}">${a.level==='HIGH'?'HIGH THREAT':'WARNING'}</span><div><strong>${a.level==='HIGH'?'Dangerous object detected':'Object entering warning zone'}</strong><div class="alert-meta">Distance ${Math.round(a.distance)} cm · Angle ${Math.round(a.angle)}°</div></div><span style="color:var(--muted);font-size:11px">${a.time}</span></div>`).join('')}
function setAlertFilter(f){alertFilter=f;renderAlerts()}
function clearAlerts(){alerts=[];lastAlertKey='';renderAlerts();toast('Alerts cleared')}

async function nextReading(){try{const r=await fetch(`/api/next?danger=${settings.danger}&warning=${settings.warning}`);if(!r.ok)throw Error();return await r.json()}catch(e){return null}}
async function radarLoop(){if(!running)return;if(sensorMode==='SIMULATION'){const d=await nextReading();if(d){current=d;rememberTarget(d);updateUI(d);addAlert(d)}}timer=setTimeout(radarLoop,Math.max(40,settings.speed))}
async function startRadar(){try{await fetch('/api/start')}catch(e){}running=true;if($('radarState'))$('radarState').innerText='● LIVE';if($('systemText'))$('systemText').innerText='SYSTEM ONLINE';page('radarPage');if(!timer)radarLoop();toast('Radar started')}
async function stopRadar(){running=false;try{await fetch('/api/stop')}catch(e){}if(timer){clearTimeout(timer);timer=null}if($('radarState'))$('radarState').innerText='● STANDBY';toast('Radar stopped')}
async function startSimulation(){sensorMode='SIMULATION';await stopRadar();await startRadar();toast('Demo running — no hardware required')}
async function generateData(){try{const r=await fetch('/api/generate');if(!r.ok)throw Error();targetMemory.clear();alerts=[];lastAlertKey='';renderAlerts();current={angle:0,distance:0,detected:false,threat:'LOW',status:'READY'};await analyze();await drawCharts();toast('New demo data generated')}catch(e){toast('Generation failed')}}

function selectConnection(kind){document.querySelectorAll('.connect-option').forEach(x=>x.classList.remove('active'));const id=kind==='simulation'?'optSimulation':'optBrowser';if($(id))$(id).classList.add('active');if($('simulationSetup'))$('simulationSetup').style.display=kind==='simulation'?'block':'none';if($('usbSetup'))$('usbSetup').style.display=kind==='browser'?'block':'none';if($('networkSetup'))$('networkSetup').style.display='none';sensorMode=kind==='simulation'?'SIMULATION':'LIVE';updateConnectStatus()}
function browserSupported(){return !!(navigator&&'serial' in navigator)}
async function connectBrowserUSB(){if(!browserSupported())return toast('Use Chrome or Edge for Arduino USB');try{browserSerialPort=await navigator.serial.requestPort();await browserSerialPort.open({baudRate:+($('serialBaud')?.value||115200)});browserSerialRunning=true;sensorMode='LIVE';updateConnectStatus();toast('Arduino connected');readBrowserSerial()}catch(e){browserSerialRunning=false;toast(e.message||'Arduino connection cancelled')}}
async function readBrowserSerial(){if(!browserSerialPort?.readable)return;const decoder=new TextDecoderStream();browserSerialPort.readable.pipeTo(decoder.writable);browserSerialReader=decoder.readable.getReader();let buffer='';while(browserSerialRunning){const {value,done}=await browserSerialReader.read();if(done)break;buffer+=value||'';const lines=buffer.split(/\r?\n/);buffer=lines.pop()||'';for(const line of lines){const x=parseReading(line);if(x)applyBrowserReading(x)}}}
function parseReading(line){line=String(line||'').trim();if(!line)return null;try{const j=JSON.parse(line);if(j.angle!=null&&j.distance!=null)return {angle:+j.angle,distance:+j.distance}}catch(e){}const m=line.match(/angle\s*[=:]\s*(-?\d+(?:\.\d+)?).*distance\s*[=:]\s*(-?\d+(?:\.\d+)?)/i);if(m)return {angle:+m[1],distance:+m[2]};const a=line.split(',').map(Number);return a.length>=2&&Number.isFinite(a[0])&&Number.isFinite(a[1])?{angle:a[0],distance:a[1]}:null}
function applyBrowserReading(x){if(x.angle<0||x.angle>180||x.distance<0||x.distance>1000)return;const d=Math.min(settings.range,x.distance);current={angle:x.angle,distance:d,detected:d<settings.warning,threat:threatClass(d),status:d<settings.danger?'DANGER':d<settings.warning?'WARNING':'SAFE'};rememberTarget(current);updateUI(current);addAlert(current);fetch('/api/sensor/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({angle:x.angle,distance:d,source:'browser-usb'})}).catch(()=>{})}
async function disconnectBrowserUSB(){browserSerialRunning=false;try{await browserSerialReader?.cancel()}catch(e){}try{await browserSerialPort?.close()}catch(e){}browserSerialReader=null;browserSerialPort=null;sensorMode='SIMULATION';updateConnectStatus()}
function refreshSerialPorts(){toast('For online use, click Connect Arduino and choose the USB device in Chrome/Edge')}
function connectArduino(){connectBrowserUSB()}
function disconnectArduino(){disconnectBrowserUSB();toast('Arduino disconnected')}
function autoConnectArduino(){toast('Use Connect Arduino for browser USB')}

async function analyze(){try{const r=await fetch(`/api/analytics?danger=${settings.danger}&warning=${settings.warning}`);const d=await r.json();[['total',d.total],['detected',d.detected],['rate',d.detection_rate+'%'],['average',d.average+' cm'],['aTotal',d.total],['aDetected',d.detected],['aRate',d.detection_rate+'%'],['aAverage',d.average+' cm'],['aHigh',d.high],['aWarning',d.warning],['aSafe',d.safe],['aPeakAngle',d.peak_angle+'°'],['aMin',d.minimum+' cm'],['aMax',d.maximum+' cm'],['aMedian',d.median+' cm'],['aThreatMix',`${d.high} / ${d.warning} / ${d.safe}`]].forEach(([id,v])=>{if($(id))$(id).innerText=v});drawThreatChart(d)}catch(e){}}
async function drawCharts(){try{const r=await fetch('/api/chart');const d=await r.json();drawLine('distanceChart',d.angle,d.cleaned,'Distance vs Angle');drawLine('angleChart',d.angle,d.raw,'Raw Distance')}catch(e){}}
function drawThreatChart(d){const c=$('threatChart');if(!c)return;const ctx=c.getContext('2d'),w=c.width,h=c.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#060a0d';ctx.fillRect(0,0,w,h);const items=[['DANGER',d.high,'#ff4057'],['WARNING',d.warning,'#ffc857'],['SAFE',d.safe,'#39ff88']],max=Math.max(1,...items.map(x=>x[1]));items.forEach((it,i)=>{const y=42+i*62;ctx.fillStyle='#8fa19a';ctx.font='11px Arial';ctx.fillText(it[0],18,y);ctx.fillStyle='#172126';ctx.fillRect(105,y-12,w-145,22);ctx.fillStyle=it[2];ctx.fillRect(105,y-12,(w-145)*(it[1]/max),22);ctx.fillStyle='#e8f0f5';ctx.fillText(String(it[1]),w-30,y+4)})}
function drawLine(id,x,y,label){const c=$(id);if(!c||!Array.isArray(y)||!y.length)return;const ctx=c.getContext('2d'),w=c.width,h=c.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#060a0d';ctx.fillRect(0,0,w,h);const pad=35,max=Math.max(settings.range,...y,1),range=max||1;ctx.strokeStyle='#1e3030';for(let i=0;i<5;i++){const yy=pad+i*(h-pad*1.4)/4;ctx.beginPath();ctx.moveTo(pad,yy);ctx.lineTo(w-12,yy);ctx.stroke()}ctx.strokeStyle='#39ff88';ctx.lineWidth=2;ctx.beginPath();y.forEach((v,i)=>{const xx=pad+i*Math.max(1,(w-pad-15)/(y.length-1||1)),yy=h-pad-(v/range)*(h-pad*1.5);i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();ctx.fillStyle='#8fa19a';ctx.font='10px Arial';ctx.fillText(label,8,15)}
function exportCSV(){location.href='/api/export'}

function uploadCSV(){const f=$('csvFile')?.files?.[0];if(!f)return;const fd=new FormData();fd.append('file',f);fetch('/api/upload',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{if(d.success){analyze();drawCharts();toast('CSV processed')}else toast(d.message||'Upload failed')}).catch(()=>toast('Upload failed'))}

function init(){safeSettings();applySettings();renderAlerts();bindNavigation();drawRadar('radar');drawRadar('radar2');animateRadar();analyze();drawCharts();updateConnectStatus();updateBrowserUSBAvailability()}
function updateBrowserUSBAvailability(){const e=$('browserUsbSupport');if(e)e.innerText=browserSupported()?'Arduino USB is supported in this browser.':'Use Chrome or Edge for Arduino USB.'}
window.page=page;window.startRadar=startRadar;window.stopRadar=stopRadar;window.startSimulation=startSimulation;window.generateData=generateData;window.selectConnection=selectConnection;window.connectBrowserUSB=connectBrowserUSB;window.disconnectBrowserUSB=disconnectBrowserUSB;window.refreshSerialPorts=refreshSerialPorts;window.connectArduino=connectArduino;window.disconnectArduino=disconnectArduino;window.autoConnectArduino=autoConnectArduino;window.saveSettings=saveSettings;window.resetSettings=resetSettings;window.setAlertFilter=setAlertFilter;window.clearAlerts=clearAlerts;window.exportCSV=exportCSV;window.uploadCSV=uploadCSV;

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();</script>
</body></html>
"""


# =========================================================
# LOGIN PAGE
# =========================================================

LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart Ultrasonic Radar - Login</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:radial-gradient(circle at 50% 20%,#102018 0,#070b0f 35%,#040608 100%);color:#e8f0f5;font-family:Segoe UI,Arial,sans-serif}.login{width:min(400px,90vw);padding:32px;background:#0b1016;border:1px solid #1d2933;border-radius:16px;box-shadow:0 20px 70px rgba(0,0,0,.45)}h1{margin:0 0 8px;text-align:center;color:#39ff88;font-size:20px;letter-spacing:2px}p{margin:0 0 24px;text-align:center;color:#7e8c98;font-size:12px;letter-spacing:1px}label{display:block;margin:12px 0 6px;color:#aab7c0;font-size:12px}input{width:100%;padding:12px;border:1px solid #27353e;border-radius:8px;background:#070d12;color:#fff;outline:none}input:focus{border-color:#39ff88}button{width:100%;margin-top:18px;padding:12px;border:1px solid #1d9c61;border-radius:8px;background:#0b1913;color:#39ff88;font-weight:700;cursor:pointer}button:hover{background:#10251a}.error{margin-top:14px;padding:10px;border-radius:7px;background:#260e13;border:1px solid #71313b;color:#ff8290;text-align:center;font-size:12px}</style>
</head>
<body>
<div class="login">
<h1>SMART ULTRASONIC RADAR</h1>
<p>MONITORING SYSTEM</p>
<form method="POST" action="/login">
<label>Username</label>
<input type="text" name="username" placeholder="Enter username" required autofocus>
<label>Password</label>
<input type="password" name="password" placeholder="Enter password" required>
<button type="submit">LOGIN</button>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
</form>
</div>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username == "Rehaan" and password == "1234":
            session["logged_in"] = True
            return redirect(url_for("home"))

        return render_template_string(LOGIN_PAGE_HTML, error="Invalid username or password")

    return render_template_string(LOGIN_PAGE_HTML, error=None)


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":

    load_data()

    print(
        "Radar Web App Started"
    )

    print(
        "Open: http://127.0.0.1:5001"
    )

    app.run(

        host="127.0.0.1",

        port=5001,

        debug=True
    ) 
