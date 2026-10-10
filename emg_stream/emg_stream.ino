// ReGain - stream EMG samples from the BioAmp EXG Pill
// Wiring: Pill OUT -> A0, Pill VCC -> 5V, Pill GND -> GND
#define SAMPLE_RATE 500
#define BAUD_RATE   115200
#define INPUT_PIN   A0

unsigned long nextSample = 0;

void setup() {
  Serial.begin(BAUD_RATE);
  nextSample = micros();
}

void loop() {
  unsigned long now = micros();
  if ((long)(now - nextSample) >= 0) {          // time for the next sample?
    nextSample += 1000000UL / SAMPLE_RATE;      // schedule the one after (every 2 ms)
    Serial.println(analogRead(INPUT_PIN));
  }
}