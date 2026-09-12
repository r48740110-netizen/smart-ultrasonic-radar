SMART ULTRASONIC RADAR - CLEAN FINAL

Run locally with Python 3.11:
  python -m pip install -r requirements.txt
  python app.py
Open http://127.0.0.1:5001

This build intentionally has ONE app.py. Do not run an app.py from a nested folder.

Features:
- Individual signup/login accounts
- Per-user radar readings
- Demo Mode
- Browser Arduino connection (Chrome/Edge Web Serial)
- Live Radar
- Safety Zone
- Alerts
- Analytics
- Settings
- CSV export

For deployment, use Render with the included render.yaml. SQLite is suitable for a college prototype but Render's free filesystem is not persistent across service replacement/redeploy.
