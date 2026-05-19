#include <BLEAdvertising.h>
#include <BLECharacteristic.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#define BUZZER_PIN 19
#define BUTTON_PIN 18
#define LED_PIN 4

const char* DEVICE_NAME = "Trackify_ESP32";
const char* ALERT_SERVICE_UUID = "9d3f0001-7b31-4f8e-9b6f-76d53d2a1001";
const char* ALERT_CHAR_UUID = "9d3f0001-7b31-4f8e-9b6f-76d53d2a1002";

const uint8_t ALERT_CLEAR = 0;
const uint8_t ALERT_FAR = 1;
const uint8_t ALERT_LOST = 2;

const unsigned long ALERT_REPEAT_MS = 1400;
const unsigned long HEARTBEAT_TIMEOUT_MS = 3500;
const unsigned long BUTTON_DEBOUNCE_MS = 250;

BLEAdvertising* advertising = nullptr;
BLECharacteristic* alertCharacteristic = nullptr;

bool clientConnected = false;
bool hadClientConnection = false;
bool buttonLatch = false;
uint8_t alertState = ALERT_CLEAR;
unsigned long lastAlertAt = 0;
unsigned long lastCommandAt = 0;
unsigned long lastButtonAt = 0;

void setOutputsIdle() {
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
}

void beep(int durationMs) {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(durationMs);
  digitalWrite(BUZZER_PIN, LOW);
}

void flashLED(int durationMs) {
  digitalWrite(LED_PIN, HIGH);
  delay(durationMs);
  digitalWrite(LED_PIN, LOW);
}

void runFarAlertPattern() {
  beep(90);
  flashLED(90);
}

void runLostAlertPattern() {
  beep(160);
  flashLED(160);
  delay(90);
  beep(160);
  flashLED(160);
}

class TrackifyServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override {
    clientConnected = true;
    hadClientConnection = true;
    alertState = ALERT_CLEAR;
    lastCommandAt = millis();
    setOutputsIdle();

    if (advertising != nullptr) {
      BLEDevice::startAdvertising();
    }

    Serial.println("Desktop application connected to Trackify.");
  }

  void onDisconnect(BLEServer* server) override {
    clientConnected = false;
    if (hadClientConnection) {
      alertState = ALERT_LOST;
      lastAlertAt = 0;
      Serial.println("Desktop application disconnected. Lost alert armed.");
    }

    if (advertising != nullptr) {
      BLEDevice::startAdvertising();
    }
  }
};

class AlertCharacteristicCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    String value = characteristic->getValue();
    if (value.length() == 0) {
      return;
    }

    uint8_t command = static_cast<uint8_t>(value[0]);
    if (command > ALERT_LOST) {
      return;
    }

    alertState = command;
    lastCommandAt = millis();
    lastAlertAt = 0;

    if (alertState == ALERT_CLEAR) {
      setOutputsIdle();
      Serial.println("Hardware alert cleared by desktop application.");
    } else if (alertState == ALERT_FAR) {
      Serial.println("Far alert received from desktop application.");
    } else {
      Serial.println("Lost alert received from desktop application.");
    }
  }
};

void setup() {
  Serial.begin(115200);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  setOutputsIdle();

  BLEDevice::init(DEVICE_NAME);

  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new TrackifyServerCallbacks());

  BLEService* alertService = server->createService(ALERT_SERVICE_UUID);
  alertCharacteristic = alertService->createCharacteristic(
    ALERT_CHAR_UUID,
    BLECharacteristic::PROPERTY_READ |
      BLECharacteristic::PROPERTY_WRITE |
      BLECharacteristic::PROPERTY_WRITE_NR
  );
  alertCharacteristic->setCallbacks(new AlertCharacteristicCallbacks());
  alertCharacteristic->setValue(&alertState, 1);
  alertService->start();

  advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(ALERT_SERVICE_UUID);

  BLEAdvertisementData advertisementData;
  advertisementData.setFlags(0x06);
  advertising->setAdvertisementData(advertisementData);

  BLEAdvertisementData scanResponseData;
  scanResponseData.setName(DEVICE_NAME);
  advertising->setScanResponseData(scanResponseData);
  advertising->setScanResponse(true);

  BLEDevice::startAdvertising();

  Serial.println("Trackify ESP32 started.");
  Serial.println("BLE advertising as: Trackify_ESP32");
  Serial.println("Alert service ready for desktop-triggered buzzer and LED commands.");

  beep(150);
  flashLED(150);
  delay(100);
  beep(150);
  flashLED(150);
}

void handleButtonSelfTest() {
  bool buttonPressed = digitalRead(BUTTON_PIN) == LOW;
  unsigned long now = millis();

  if (buttonPressed && !buttonLatch && (now - lastButtonAt) >= BUTTON_DEBOUNCE_MS) {
    Serial.println("Button pressed. Running local hardware self-test.");
    beep(100);
    flashLED(100);
    lastButtonAt = now;
  }

  buttonLatch = buttonPressed;
}

void handleHeartbeatTimeout() {
  if (!clientConnected || !hadClientConnection) {
    return;
  }

  unsigned long now = millis();
  if ((now - lastCommandAt) >= HEARTBEAT_TIMEOUT_MS) {
    alertState = ALERT_LOST;
    lastAlertAt = 0;
    clientConnected = false;
    Serial.println("Heartbeat timeout detected. Lost alert armed.");
  }
}

void handleAlertOutputs() {
  if (alertState == ALERT_CLEAR) {
    setOutputsIdle();
    return;
  }

  unsigned long now = millis();
  if ((now - lastAlertAt) < ALERT_REPEAT_MS) {
    return;
  }

  if (alertState == ALERT_FAR) {
    runFarAlertPattern();
  } else {
    runLostAlertPattern();
  }

  lastAlertAt = millis();
}

void loop() {
  handleButtonSelfTest();
  handleHeartbeatTimeout();
  handleAlertOutputs();
  delay(20);
}
