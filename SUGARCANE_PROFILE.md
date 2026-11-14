# Sugarcane profile — Kapriwas, Haryana

## Project profile

- Location: Kapriwas, Rewari, Haryana (28.24331, 76.80864)
- Crop: Sugarcane
- Growth stage: Unspecified; confirm whether the field is unplanted, newly planted, tillering, grand growth, maturity, or ratoon.
- Soil type: Unspecified; get a soil test and field description before setting agronomic thresholds.
- Prototype event settings: rain onset 0.1 mm, temperature drop 2 °C over 6 hours, 6-hour cooldown. These are generic weather-event settings, not validated sugarcane irrigation or frost thresholds.

## Research summary

ICAR's 2025 farmer advisory describes spring planting from February to mid-March, with later planting up to May only when irrigation is assured. It says irrigation frequency should account for soil and rainfall, and gives a higher regional irrigation count for western than eastern Haryana. Fertilizer should be adjusted using soil-test results. [ICAR Kharif Agro-Advisory 2025](https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf)

A field study at CCS HAU's Regional Research Station in Karnal evaluated sugarcane under different available-soil-moisture regimes. Its results support monitoring moisture during establishment and active growth rather than inferring crop stress from air temperature alone. Experimental moisture treatments are not thresholds for this project's normalized Wokwi controls or an uncalibrated field sensor. [ICAR/CCS HAU Karnal study](https://epubs.icar.org.in/index.php/IJAgS/article/download/90437/47588/331234)

HAU's Karnal research station describes a half-ridge planting approach: place setts in dry furrows, cover lightly, irrigate to about half the ridge, then finish the ridge after several days. This is a regional method to discuss with an agronomist, not a universal instruction for every field. [CCS HAU technology note](https://hau.ac.in/public/pages-pdf/1757309507.pdf)

## Monitoring priorities

1. **Moisture stress:** trend shallow and deeper soil moisture alongside temperature, light/radiation, rain, and recorded irrigation. Prompt a field check; don't trigger irrigation from an uncalibrated percentage.
2. **Excess rain and drainage:** combine rain with persistently high root-zone moisture and ask the grower to check standing water and drainage. Rain by itself does not prove waterlogging.
3. **Cold/frost watch:** use forecast minimum temperature and crop stage to prompt inspection. Confirm thresholds with local extension guidance before treating a numeric value as a crop-damage boundary.
4. **Lodging context:** add wind-gust forecasts and crop stage if this becomes a goal; the current simulated sensor channels do not directly measure lodging.
5. **Pests and disease:** require grower observations or images. Environmental readings cannot confirm an infestation or disease.

The existing simulated channels—air temperature/humidity, two soil-moisture depths, soil temperature, light, rainfall, and leaf wetness—are a useful environmental baseline. Real use would also benefit from calibrated soil moisture, irrigation records, field scouting, planting date/growth stage, and a soil test (including pH and salinity/EC).

## What alerts mean

The deployed automatic rule detects rain onset following a temperature drop. It is a generic weather alert, not a sugarcane diagnosis. The dashboard's **Create demo alert** control inserts an explicitly labeled synthetic sugarcane event for presentations. It does not represent real field conditions.

For Rewari-area local guidance, CCS HAU lists KVK Bawal at **18001804002**. [CCS HAU Farm Advisory Service](https://hau.ac.in/page/farm-advisory-service)

## Receiving notifications

- Open `http://localhost:8000` while the Docker stack is running.
- Click **Enable notifications** and allow browser notifications.
- New events are checked every 10 seconds and shown in the dashboard; with browser permission, a desktop notification is also sent while the dashboard page is open.
- Click **Create demo alert** to demonstrate the notification and research flow without waiting for a weather condition. The alert is labeled DEMO ONLY.
- Email, SMS, and push delivery while the dashboard is closed are not configured.
