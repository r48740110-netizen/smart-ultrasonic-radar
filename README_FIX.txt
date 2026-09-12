SMART ULTRASONIC RADAR - CLICK/RADAR FIX

Replace your current app_online.py with this app_online.py.

Run from the Smart Ultrasonic App folder:
  python app_online.py

Open exactly:
  http://127.0.0.1:5002

The fix makes JavaScript startup safer, binds navigation after DOM ready,
keeps dashboard buttons as real buttons, initializes the radar canvas safely,
and avoids the Flask reloader/debugger issue.
