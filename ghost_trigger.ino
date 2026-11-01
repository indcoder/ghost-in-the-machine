/*
  Ghost in the Machine -- Hacktoberfest, Arduino UNO Q (not UNO R4 WiFi).

  QRB2210 / Debian owns Wi-Fi; STM32U585 / Zephyr runs this sketch and the
  built-in 8x13 blue matrix. Arduino_RouterBridge notifications ask the App Lab
  Python helper to GET /haunt, then return the result without blocking loop().
  No WiFi.begin() or MCU HTTPClient is assumed to access the MPU's radio.

  SETUP (App Lab app layout is included alongside this standalone sketch):
  1. On UNO Q, connect Wi-Fi in App Lab, or on its Linux terminal run:
       nmcli device wifi connect '<WIFI_SSID>' --ask
     Enter <WIFI_PASS> at the prompt; do not store real credentials here.
  2. Set SERVER_URL in python/main.py to http://<PC_IP>:8000/haunt.
     PC_IP is the PC's LAN IPv4 address, NOT localhost on the UNO Q.
     FastAPI must listen on 0.0.0.0:8000; allow port 8000 on the trusted LAN.
     Plain HTTP is intentional for this local demo; do not expose it publicly.
  3. Open/import this directory as an App Lab app, then Run both components.
     sketch/sketch.ino is an identical App Lab copy; keep it in sync if editing.
     Arduino_LED_Matrix comes with arduino:zephyr. ArduinoGraphics and
     Arduino_RouterBridge are declared in sketch/sketch.yaml.
  4. Open App Lab's Serial monitor (Serial is the UNO Q Bridge monitor alias).

  Polling, response expiry, and scrolling use rollover-safe millis() timers.
  The helper trims surrounding whitespace; the sketch also trims defensively.
  Exactly "dormant", empty strings, and repeats of the last shown text do nothing.
  A new critique replaces any unfinished scroll; Serial preserves the full text.
  Scrolling uses ArduinoGraphics Font_5x7 and a 104-byte matrix.draw() canvas.
  endText(SCROLL_LEFT) is supported, but blocks; NO_SCROLL renders one timed frame.
  Set ENABLE_MATRIX_SCROLL=false for Serial-only output without moving text.

  Docs:
  https://docs.arduino.cc/tutorials/uno-q/user-manual/
  https://docs.arduino.cc/libraries/arduinographics/
  https://github.com/arduino/ArduinoCore-zephyr/tree/main/libraries/Arduino_LED_Matrix
  https://github.com/arduino-libraries/Arduino_RouterBridge
  https://github.com/arduino/app-bricks-examples/tree/main/core-and-foundational/03-bridge-basics

  Validation: reviewed against official APIs; UNO Q compilation/hardware testing
  still required (no arduino-cli/toolchain available on the authoring host).
  Built with accessibility in mind, but manual accessibility review is needed.
*/

#include <ArduinoGraphics.h>
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

// Configuration: Wi-Fi credentials live on Linux; URL lives in python/main.py.
constexpr unsigned long POLL_INTERVAL_MS = 10000UL;
constexpr unsigned long RESPONSE_TIMEOUT_MS = 5000UL;
constexpr unsigned long SCROLL_STEP_MS = 75UL;
constexpr unsigned int MAX_MESSAGE_BYTES = 192;
constexpr bool ENABLE_MATRIX_SCROLL = true;

Arduino_LED_Matrix matrix;

// Render all 104 pixels explicitly, rather than using UNO R4 packed frames.
class MatrixCanvas : public ArduinoGraphics {
public:
  MatrixCanvas() : ArduinoGraphics(13, 8) {}

  void set(int x, int y, uint8_t r, uint8_t g, uint8_t b) override {
    if (x >= 0 && x < 13 && y >= 0 && y < 8) {
      pixels[y * 13 + x] = (r | g | b) ? 1 : 0;
    }
  }

  void endDraw() override {
    matrix.draw(pixels);
  }

private:
  uint8_t pixels[104] = {};
};

MatrixCanvas canvas;
String lastDisplayed;
String scrollMessage;
String receivedMessage;
bool receivedOk = false;
bool resultReady = false;
bool waiting = false;
bool scrolling = false;
unsigned long requestId = 0;
unsigned long lastPoll = 0;
unsigned long requestStarted = 0;
unsigned long lastFrame = 0;
int scrollX = 13;

// provide_safe runs this callback on the sketch thread through its loop hook.
void receiveHaunt(unsigned long id, bool ok, String message) {
  if (!waiting || id != requestId) {
    return;  // Ignore delayed replies to expired requests.
  }
  waiting = false;
  receivedOk = ok;
  receivedMessage = message;
  resultReady = true;
}

void clearMatrix() {
  canvas.clear();
  canvas.endDraw();
}

void handleResult() {
  resultReady = false;
  if (!receivedOk) {
    Serial.print("Haunt request failed: ");
    Serial.println(receivedMessage);
    return;
  }

  receivedMessage.trim();
  if (receivedMessage.length() == 0 || receivedMessage == "dormant" ||
      receivedMessage == lastDisplayed) {
    return;
  }
  if (receivedMessage.length() > MAX_MESSAGE_BYTES) {
    Serial.println("Haunt request failed: message too long");
    return;
  }

  lastDisplayed = receivedMessage;
  Serial.println(lastDisplayed);
  if (ENABLE_MATRIX_SCROLL) {
    scrollMessage = lastDisplayed;
    scrollX = 13;
    scrolling = true;
    lastFrame = millis() - SCROLL_STEP_MS;
  }
}

void updateScroll(unsigned long now) {
  if (!scrolling || now - lastFrame < SCROLL_STEP_MS) {
    return;
  }
  lastFrame = now;
  canvas.clear();
  canvas.beginText(scrollX, 0, 255, 255, 255);
  canvas.print(scrollMessage);
  canvas.endText(NO_SCROLL);  // One frame only; no delay() or blocking scroll.
  --scrollX;
  const int textWidth = static_cast<int>(scrollMessage.length()) * canvas.textFontWidth();
  if (scrollX < -textWidth) {
    scrolling = false;
    clearMatrix();
  }
}

void setup() {
  Bridge.begin();
  Serial.begin(115200);
  matrix.begin();
  matrix.setGrayscaleBits(1);
  canvas.begin();
  canvas.textFont(Font_5x7);
  clearMatrix();
  Bridge.provide_safe("ghost_haunt_result", receiveHaunt);
  lastPoll = millis();  // First poll after 10 seconds; Python can finish starting.
  Serial.println("Ghost ready: Linux Wi-Fi + App Lab Python bridge required.");
}

void loop() {
  if (resultReady) {
    handleResult();
  }
  const unsigned long now = millis();
  if (waiting && now - requestStarted >= RESPONSE_TIMEOUT_MS) {
    waiting = false;
    Serial.println("Haunt request timed out; retrying on next 10-second tick.");
  }
  if (!waiting && now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;
    requestStarted = now;
    waiting = true;
    ++requestId;
    Bridge.notify("ghost_fetch_haunt", requestId);
  }
  updateScroll(now);
}
