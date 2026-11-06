#include <WiFi.h>

// Wi-Fi credentials live in secrets.h, which is gitignored.
// Copy secrets.h.example to secrets.h and fill it in before flashing.
#include "secrets.h"

static const int TMS_PIN = 18;
static const int TCK_PIN = 19;
static const int TDI_PIN = 23;
static const int TDO_PIN = 35;

static WiFiServer server(2542);
static WiFiClient client;

static void jtag_init() {
  pinMode(TDO_PIN, INPUT);
  pinMode(TDI_PIN, OUTPUT);
  pinMode(TCK_PIN, OUTPUT);
  pinMode(TMS_PIN, OUTPUT);
  digitalWrite(TDI_PIN, 0);
  digitalWrite(TCK_PIN, 0);
  digitalWrite(TMS_PIN, 1);
}

static int read_all(uint8_t* buf, int len) {
  int total = 0;
  unsigned long start = millis();
  while (total < len && millis() - start < 10000) {
    int avail = client.available();
    if (avail > 0) {
      int r = client.read(buf + total, min(avail, len - total));
      if (r > 0) {
        total += r;
        start = millis();
      }
    } else {
      delay(1);
    }
  }
  return total;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("JTAG XVC starting...");
  jtag_init();
  WiFi.begin(MY_SSID, MY_PASSPHRASE);
  Serial.print("WiFi connecting");
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 60) {
    delay(500);
    Serial.print(".");
    tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nIP: %s\n", WiFi.localIP().toString().c_str());
    server.begin();
    Serial.println("XVC on :2542");
  } else {
    Serial.println("\nWiFi FAIL");
  }
}

void loop() {
  if (!client || !client.connected()) {
    client = server.available();
    return;
  }
  
  if (client.available() < 1) {
    delay(1);
    return;
  }
  
  uint8_t hdr[6];
  if (read_all(hdr, 1) < 1) return;
  
  if (hdr[0] == 'g') {
    if (read_all(hdr + 1, 7) < 7) return;
    client.print("xvcServer_v1.0:2048\n");
    client.flush();
  }
  else if (hdr[0] == 's' && client.peek() == 'e') {
    if (read_all(hdr + 1, 6) < 6) return;
    uint8_t period_buf[4];
    if (read_all(period_buf, 4) < 4) return;
    client.write(period_buf, 4);
    client.flush();
  }
  else if (hdr[0] == 's' && client.peek() == 'h') {
    if (read_all(hdr + 1, 5) < 5) return;
    uint8_t len_buf[4];
    if (read_all(len_buf, 4) < 4) return;
    
    uint32_t len = ((uint32_t)len_buf[3]<<24) | ((uint32_t)len_buf[2]<<16) |
                   ((uint32_t)len_buf[1]<<8) | len_buf[0];
    uint32_t nr_bytes = (len + 7) / 8;
    
    if (nr_bytes > 16384) {
      uint8_t discard[256];
      for (uint32_t i = 0; i < nr_bytes * 2; ) {
        uint32_t chunk = min((uint32_t)256, nr_bytes * 2 - i);
        if (read_all(discard, chunk) < (int)chunk) return;
        i += chunk;
      }
      uint8_t z = 0;
      client.write(&z, 1);
      client.flush();
      return;
    }
    
    uint8_t* tms_buf = (uint8_t*)malloc(nr_bytes);
    uint8_t* tdi_buf = (uint8_t*)malloc(nr_bytes);
    uint8_t* tdo_buf = (uint8_t*)calloc(nr_bytes, 1);
    
    if (!tms_buf || !tdi_buf || !tdo_buf) {
      free(tms_buf); free(tdi_buf); free(tdo_buf);
      return;
    }
    
    if (read_all(tms_buf, nr_bytes) < (int)nr_bytes) { free(tms_buf); free(tdi_buf); free(tdo_buf); return; }
    if (read_all(tdi_buf, nr_bytes) < (int)nr_bytes) { free(tms_buf); free(tdi_buf); free(tdo_buf); return; }
    
    for (uint32_t bit = 0; bit < len; bit++) {
      int byte_idx = bit / 8;
      int bit_idx = bit % 8;
      int tms_bit = (tms_buf[byte_idx] >> bit_idx) & 1;
      int tdi_bit = (tdi_buf[byte_idx] >> bit_idx) & 1;
      
      digitalWrite(TCK_PIN, 0);
      digitalWrite(TMS_PIN, tms_bit);
      digitalWrite(TDI_PIN, tdi_bit);
      delayMicroseconds(1);
      digitalWrite(TCK_PIN, 1);
      delayMicroseconds(2);
      
      int tdo_bit = digitalRead(TDO_PIN) & 1;
      if (tdo_bit) tdo_buf[byte_idx] |= (1 << bit_idx);
    }
    digitalWrite(TCK_PIN, 0);
    
    client.write(tdo_buf, nr_bytes);
    client.flush();
    free(tms_buf); free(tdi_buf); free(tdo_buf);
  }
  else {
    uint8_t discard[64];
    int avail = client.available();
    if (avail > 0) client.read(discard, min(avail, 64));
  }
}
