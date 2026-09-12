#include <Servo.h>

Servo radarServo;
const int trigPin = 9;
const int echoPin = 10;
const int servoPin = 6;

long readDistanceCm() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  long duration = pulseIn(echoPin, HIGH, 30000);
  if (duration == 0) return 400;

  long distance = duration * 0.0343 / 2;
  if (distance < 2) distance = 2;
  if (distance > 400) distance = 400;
  return distance;
}

void setup() {
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  radarServo.attach(servoPin);
  Serial.begin(115200);
}

void loop() {
  for (int angle = 0; angle <= 180; angle += 2) {
    radarServo.write(angle);
    delay(25);
    Serial.print(angle);
    Serial.print(',');
    Serial.println(readDistanceCm());
  }

  for (int angle = 180; angle >= 0; angle -= 2) {
    radarServo.write(angle);
    delay(25);
    Serial.print(angle);
    Serial.print(',');
    Serial.println(readDistanceCm());
  }
}
