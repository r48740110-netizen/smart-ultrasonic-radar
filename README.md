# Smart Ultrasonic Radar — Final

Student-facing Flask web app for an Arduino ultrasonic radar.

## Student flow
1. Sign up / log in
2. Connect Arduino from Chrome/Edge
3. Select the Arduino serial port
4. Arduino sends `angle,distance` lines at 115200 baud
5. Live Radar, Safety, Alerts and Analytics update automatically
6. Export readings as CSV

Demo Mode works without hardware.

## Local
Use Python 3.11+.

```bash
python -m pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5001

## Production
Render uses PostgreSQL through `DATABASE_URL`, secure cookies, a generated secret, Gunicorn and `/api/health`.

Web Serial requires a secure context (HTTPS) and a supported browser. The production Render URL is HTTPS.

## Arduino serial format
Either:
- `90,35`
- `{"angle":90,"distance":35}`

Baud rate: 115200.
