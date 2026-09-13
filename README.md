# Dexcom Webserver (All-in-One Blood Glucose Dashboard & Cast Server)

> 🇸🇪 *Svenska instruktioner finns längre ner i dokumentet / Swedish instructions further down.*

---

A lightweight, self-hosted, all-in-one blood glucose monitoring web application for **Dexcom Share**. It features a live 2-page touch/swipe dashboard, 12-hour historical trend graph with Time-in-Range (TIR) statistics, a built-in configuration UI with credential testing, and an intelligent Cast Keep-Alive service to continuously stream to a **Google Nest Hub** or **Chromecast**.

**Everything runs locally on your own machine. No external cloud, no telemetry, and no third-party servers.**

---

## 🌟 Key Features

* **Built-in Web Server**: Powered by Python and Flask (running on port 8080 by default). Replaces external web servers like Nginx or Apache.
* **100% Optional Casting**: You **do not** need a Google Nest Hub or Chromecast to use this! You can use it as a standalone local web dashboard on any phone, tablet, wall-mounted iPad, or computer browser.
* **Two-Page Swipeable Dashboard**:
  * **Page 1 (Glance)**: Extra large blood glucose value, trend direction arrow (⇈, ↑, ↗, →, ↘, ↓, ⇊), delta change (e.g., `+0.4`), and time elapsed since last reading.
  * **Page 2 (Analytics)**: Smooth 12-hour Canvas graph, target glucose range visualizer (3.9 – 10.0 mmol/L), and real-time Time In Range (TIR: In Range %, High %, Low %).
  * **Auto-Return**: Automatically switches back to Page 1 after 60 seconds of inactivity on Page 2.
* **Smart Dexcom Fetcher**: Hybrid querying algorithm that checks for single-point updates every 60 seconds and syncs full 12-hour historical readings every 5–30 minutes, keeping network overhead minimal.
* **Web Configuration UI (`/config`)**:
  * In-browser setup for Dexcom Share credentials and region (Europe/OUS vs US).
  * **"Test Dexcom Connection" button**: Verifies your username, password, and region before saving, displaying your live glucose value immediately upon success.
  * **Automatic Cast Discovery**: Scans your local network in the background on startup so your Google Nest Hub or Chromecast appears in a drop-down list ready to select.
* **Intelligent Cast Keep-Alive**:
  * Continuously monitors your selected Cast device every 10 seconds.
  * Feeds an in-memory generated digital silent audio loop (`/silence.wav`) to prevent the Nest Hub from sleeping or timing out back to ambient/clock mode.
  * **Media-Aware**: If you play Spotify, YouTube, radio, or videos on your Nest Hub, the keep-alive automatically stands by and will only resume casting once the device returns to idle.
* **Privacy by Design**:
  * All credentials (`config.json`) and readings (`data.json`) are stored strictly on your local disk.
  * No telemetry, no third-party tracking, and no external proxies. The script communicates only with the official, encrypted Dexcom Share servers.

---

## 🚀 Quick Start (Windows)

1. Download or clone this repository to your computer.
2. Open the `Dexcom_webserver` folder.
3. Double-click on **`start_server.bat`**.
   * *If Python is missing, the script will automatically install it.*
   * *Required packages (`flask`, `pydexcom`, `pychromecast`) will be installed automatically.*
4. Open your web browser and navigate to:
   👉 **`http://localhost:8080/config`**
5. Enter your Dexcom account details, click **"Test Dexcom Connection"**, choose your Cast device (if applicable), and click **"Save & Apply"**.
6. View your dashboard at **`http://localhost:8080/`** (or across your home network at `http://<your-computer-ip>:8080/`).

---

## 🐧 Quick Start (Linux, macOS, Raspberry Pi)

```bash
# 1. Clone repository
git clone https://github.com/your-username/Dexcom_webserver.git
cd Dexcom_webserver

# 2. Install dependencies
pip install flask pydexcom pychromecast

# 3. Start server
python dexcom_webserver.py
```

Then open `http://localhost:8080/config` in your browser to configure your account.

---

## 🛠 How It Works Under the Hood

```
┌─────────────────────────────────────────────────────────────┐
│                      dexcom_webserver.py                     │
│                                                             │
│   ┌───────────────────┐        ┌────────────────────────┐   │
│   │   DexcomFetcher   │        │     CastKeepAlive      │   │
│   │ (Background Thread)        │   (Background Thread)  │   │
│   └─────────┬─────────┘        └───────────┬────────────┘   │
│             │ Polls every 60s              │ Every 10s      │
│             ▼                              ▼                │
│     Dexcom Share API           Google Nest Hub / Chromecast │
│     (Official HTTPS)              (DashCast Protocol)       │
│             │                              ▲                │
│             ▼                              │                │
│       data.json (Local)                    │ Streams UI     │
│             │                              │                │
│   ┌─────────▼──────────────────────────────┴────────────┐   │
│   │               Flask WSGI Web Server                 │   │
│   │                    (Port 8080)                      │   │
│   │                                                     │   │
│   │   /         -> 2-Page Swipe Dashboard               │   │
│   │   /config   -> Interactive Settings & Test UI       │   │
│   │   /api/data -> Real-time JSON data & 12h history    │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔒 Privacy & Security

* **No Middlemen**: Communications only happen directly between your machine and Dexcom's official API over TLS/HTTPS.
* **Local Storage Only**: Your credentials and blood sugar readings remain on your machine in `config.json` and `data.json`.
* **Open Source**: Every line of code is available for inspection in a single, transparent script.

---
---

# 🇸🇪 Svenska Instruktioner

Ett komplett, fristående och lokalt blodsocker- och display-system för **Dexcom Share**. Systemet innehåller en interaktiv 2-sidig swipe-dashboard, 12-timmars graf med Time In Range-statistik (TIR), en inbyggd inställningssida med inloggningstest och en Keep-Alive-tjänst för kontinuerlig visning på **Google Nest Hub** eller **Chromecast**.

**Allting körs lokalt på din egen dator. Ingen data skickas någonsin till tredje part eller externa molntjänster.**

---

## 🌟 Huvudfunktioner

* **Inbyggd webbserver**: Byggd med Python och Flask (port 8080 som standard). Ersätter helt externa webbservrar som Nginx eller Apache.
* **Valfri Casting (Cast är inte ett krav)**: Du **måste inte** ha en Google Nest Hub eller Chromecast! Du kan använda systemet som en ren webbserver och öppna blodsockervisningen i vilken webbläsare som helst (mobil, surfplatta, väggmonterad iPad eller dator).
* **2-sidig swipe-dashboard**:
  * **Sida 1 (Snabbvy)**: Mycket stort och tydligt blodsocker, trendpil (⇈, ↑, ↗, →, ↘, ↓, ⇊), delta-ändring (t.ex. `+0.4`) och tidsangivelse sedan senaste mätningen.
  * **Sida 2 (Analys)**: 12 timmars mjuk Canvas-graf, visuella målområden (3.9 – 10.0 mmol/L) och realtidsberäknad Time In Range (TIR: Inom mål %, Högt %, Lågt %).
  * **Automatisk återgång**: Om skärmen lämnas orörd på sida 2 i 60 sekunder återgår den automatiskt och mjukt till sida 1.
* **Optimerad Dexcom-hämtare**: Hybridalgoritm som kontrollerar efter nya enskilda mätvärden var 60:e sekund och synkroniserar full 12-timmarshistorik var 5–30 minut för att minimera onödig nätverkstrafik.
* **Inställningssida via webben (`/config`)**:
  * Konfigurera Dexcom Share-konto (användarnamn, lösenord och region OUS/US).
  * **"🧪 Testa Dexcom-inloggning"**: Verifierar omedelbart dina inloggningsuppgifter mot Dexcom och visar ditt aktuella blodsocker direkt på sidan innan du sparar.
  * **Automatisk sökning efter Cast-enheter**: Söker automatiskt på ditt lokala nätverk vid serverstart och fyller i en rullista med dina Google Nest Hub / Chromecast-enheter.
* **Intelligent Cast Keep-Alive**:
  * Övervakar den valda enheten var 10:e sekund via DashCast-protokollet.
  * Matar en digital tyst ljudström (`/silence.wav` som skapas direkt i datorns internminne) för att förhindra att skärmen slocknar eller återgår till klockläge/viloläge.
  * **Pausar vid media**: Spelar du Spotify, YouTube eller radio på din Nest Hub känner skriptet av detta och låter din musik spela ostört. Displayen återaktiveras först när enheten blir ledig igen.
* **Integritet och datasäkerhet**:
  * Inloggningsuppgifter (`config.json`) och mätdata (`data.json`) lagras uteslutande lokalt på din egen hårddisk.
  * Ingen telemetri, inga externa molntjänster eller spårning. Skriptet pratar enbart direkt med Dexcoms officiella krypterade HTTPS-servrar.

---

## 🚀 Snabbstart (Windows)

1. Ladda ner eller klona detta repository.
2. Öppna mappen `Dexcom_webserver`.
3. Dubbelklicka på **`start_server.bat`**.
   * *Om Python saknas laddas det ner och installeras automatiskt.*
   * *Alla nödvändiga Python-paket installeras automatiskt.*
4. Öppna valfri webbläsare och gå till:
   👉 **`http://localhost:8080/config`**
5. Fyll i dina Dexcom-inloggningsuppgifter, klicka på **"🧪 Testa Dexcom-inloggning"**, välj din Cast-enhet (om du vill casta) och klicka på **"Spara och Tillämpa"**.
6. Se din dashboard på **`http://localhost:8080/`** (eller från valfri enhet på ditt hemmanätverk via `http://<datorns-ip>:8080/`).

---

## 🐧 Snabbstart (Linux, macOS, Raspberry Pi)

```bash
# 1. Klona projektet
git clone https://github.com/ditt-anvandarnamn/Dexcom_webserver.git
cd Dexcom_webserver

# 2. Installera beroenden
pip install flask pydexcom pychromecast

# 3. Starta servern
python dexcom_webserver.py
```

Öppna sedan `http://localhost:8080/config` i webbläsaren.

---

## 💡 Bra att veta & Vanliga frågor

* **Måste jag ha en Google Nest Hub?**  
  Nej, det är helt valfritt. Du kan ha sidan öppen på din mobil, surfplatta eller datorskärm. Lämna bara enhetsfältet tomt i inställningarna om du inte vill casta.
* **Hur vet jag vilken region jag ska välja?**  
  Om du bor i Sverige eller Europa väljer du **`ous`** (Outside US). Bor du i USA väljer du **`us`**.
* **Var sparas mina uppgifter?**  
  Dina inloggningsuppgifter sparas i en fil som heter `config.json` i samma mapp som skriptet på din egen dator. Ingen annan har tillgång till den.
* **Hur stänger jag av servern?**  
  Tryck `Ctrl + C` i terminalfönstret eller stäng bara kommandotolksfönstret.

---

## 📄 Licens

Detta projekt är öppen källkod och licensierat under MIT License.
