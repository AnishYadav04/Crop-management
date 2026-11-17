# Crop Care System classroom demonstration

Open http://localhost:8000 and refresh the page. Keep Docker Desktop and Wokwi running.

## Before presenting

1. Open the `esp32` folder in VS Code and choose **Wokwi: Start Simulator**.
2. If the simulator was stopped, wait for MicroPython to boot and run this from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe scripts\upload_wokwi.py
   ```

3. Click **inside the Wokwi circuit area**, near the components, and press **F** to fit the circuit. If it is tiny, maximise VS Code first. Clicking outside the circuit will not focus its shortcuts.
4. On the website, select your plant and growth stage. The location stays Kapriwas.
5. In the graph choose **Wokwi sensors (simulation)**, **Shallow soil moisture**, **Every reading** and **Last hour**.

## Which component to change

| Component in the circuit | What changes |
| --- | --- |
| White DHT22 above the middle knobs | Click it and move its temperature or humidity slider. |
| Top middle knob, connected to D34 | Shallow soil moisture; use this for the easiest alert demonstration. |
| Middle knob directly below it, connected to D33 | Deep soil moisture. |
| Top right knob, connected to VP | Simulated rainfall amount per sample. |
| Bottom right knob, connected to VN | Leaf wetness index. |
| Blue sensor at the upper right | Soil temperature control. |
| Photoresistor below the knobs | Light control. |

Click a moisture knob, then use **Left/Right** for fine adjustment, **Page Up/Page Down** for larger changes, or **Home/End** for minimum/maximum. Watch the website's percentage rather than treating the knob's raw 0–1023 value as a percentage. [Wokwi knob controls](https://docs.wokwi.com/parts/wokwi-potentiometer), [DHT22 controls](https://docs.wokwi.com/parts/wokwi-dht22).

## A five minute demonstration

1. Set shallow moisture near **50%** and explain that the graph shows incoming MQTT samples. A flat line means the control has stayed constant.
2. Lower it below the default **30%** limit. Keep it there for at least three readings spanning 20 seconds. The monitor runs every 15 seconds and the page refreshes every 10 seconds, so allow roughly **30–45 seconds** after a successful change.
3. Show **Check root-zone moisture before watering** in **Actions for your plants**. Expand its readings to show why it appeared.
4. Ask the chatbot: **“Why did this action appear, and what should I check first?”** Its answer should distinguish simulated field readings from Kapriwas weather estimates.
5. Click **Record what you did** and enter “Inspected simulated root zone.” Use **Resolve after inspection** when finished. This records a human decision; it does not change the sensor reading.
6. For a second scenario, raise shallow moisture above **80%**. Show the new **Inspect drainage before adding more water** suggestion. This was verified with a 90% reading on 17 November 2026.

The same unresolved alert is not duplicated. After resolving an alert, that same condition also has a one-hour repeat limit. Alternate dry and wet scenarios for the presentation instead of expecting a new alert every time you turn the same knob. Change the plant profile before the demonstration, not halfway through an alert explanation.

## Other things to explain

- **Temperature graph:** select Air temperature, click the DHT22 and change its temperature. Temperature alone does not trigger the soil-moisture rules.
- **Wet canopy:** set DHT22 humidity to at least 85% and leaf wetness to at least 60%. Keep both there for three fresh readings. The action suggests inspection, not a disease diagnosis.
- **Weather panel:** Open-Meteo gives regional model estimates and a rain outlook. It continues independently of the Wokwi device.
- **Alert history and guidance:** an alert is a recorded condition; guidance is the agent's explanation with sources. “Reviewed,” “Resolved” and “Guidance available” describe different things. Old sugarcane demonstration records are retained only in **All history**.
- **Offline demonstration:** stop Wokwi at the end. After about two minutes without readings, the device is shown offline. The application does not replace its values with weather API data.
- **Forecasting:** saved models forecast environmental variables using historical weather. The inspection alerts are separate rules. Neither predicts a confirmed crop disease.

The thresholds are simulation settings. There is no physical irrigation pump, and no real crop should be watered or treated solely from these controls.
