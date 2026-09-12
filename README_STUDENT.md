# Smart Ultrasonic Radar — Student Guide

## What this app does
Connect an Arduino ultrasonic radar project to the web app and get a live professional dashboard with Radar, Safety, Alerts and Analytics.

## Student flow
1. Open the deployed HTTPS website in Chrome or Edge.
2. Create an account and log in.
3. Connect Arduino to the laptop by USB.
4. Upload `arduino_example.ino` (or use your own sketch that sends `angle,distance`).
5. Open **Connect Your Project** → **Connect Arduino**.
6. Select the Arduino serial port.
7. Start monitoring.

## Serial format
The app accepts either:
- CSV: `75,42`
- JSON: `{"angle":75,"distance":42}`

Default serial speed: **115200 baud**.

## Without hardware
Use **Demo Mode** for a presentation or UI demonstration.

## Hardware example
- Arduino Uno/Nano
- HC-SR04 ultrasonic sensor
- Servo motor
- USB cable

The example sketch uses:
- HC-SR04 TRIG = D9
- HC-SR04 ECHO = D10
- Servo signal = D6

You can change the pins in the sketch for your own wiring.
