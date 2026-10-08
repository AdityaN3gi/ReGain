// ReGain - Step 1: stream EMG samples from the BioAmp EXG Pill
// Wiring: Pill OUT -> A0, Pill VCC -> 5V, Pill GND -> GND
// Upload this, then open Serial Monitor (115200 baud) and flex your arm.

#define SAMPLE_RATE 500     // samples per second
#define BAUD_RATE   115200
#define INPUT_PIN   A0

void setup() {
  Serial.begin(BAUD_RATE);
}

void loop() {
  // Simple timer so we send one sample every 2 ms (500 Hz)
  static unsigned long past = 0;
  unsigned long present = micros();
  unsigned long interval = present - past;
  past = present;

  static long timer = 0;
  timer -= interval;

  if (timer < 0) {
    timer += 1000000L / SAMPLE_RATE;
    Serial.println(analogRead(INPUT_PIN));   // one number per line, 0-1023
  }
}
