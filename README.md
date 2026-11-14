# Brahmaputra Flood Waltz

An open-source, interactive map of the **2026 monsoon floods on the Brahmaputra floodplain in Assam, India**, built from **NISAR L-band radar** data. Made for the NASA Space Apps Challenge 2026, challenge *Dancing with the SARs*.

Monsoon clouds hide the ground from optical satellites exactly when floods happen. NISAR's L-band synthetic aperture radar sees through cloud, day and night. Calm floodwater reflects the radar pulse away from the satellite and shows up dark, so we can map where water spreads and drains, pass by pass.

## What the app does
- **Timeline**: play an animation of open water for every NISAR pass from June to October 2026.
- **Before / after**: swipe between any two dates.
- **Change map**: newly flooded land, receded water and permanent water compared with the first pass.
- **Flood frequency**: how often each place was under water during the season.
- **False colour radar** (R = HH, G = HV, B = HH/HV) and raw HH backscatter, over optical imagery or street basemaps.
- **Time-series chart** of flooded area, plus plain-language notes on the physical drivers.

## Study area
Central Assam, India: lon 93.0 to 94.6 E, lat 26.3 to 27.2 N (Kaziranga, Majuli, Morigaon, Nagaon). Period 17 June to 31 October 2026.

## Method
1. Search ASF DAAC for NISAR L2 **GCOV** granules (frequency A, L-band) over the box with `asf_search`. Keep one repeat-pass track and frame (track 163, descending, frame 75) so every date has the same viewing geometry. Skip BETA and urgent-response products and keep the newest version per date.
2. Stream only the needed pixels of `HHHH` and `HVHV` (RTC gamma-naught backscatter, 10 m posting) from each HDF5 file over HTTPS.
3. Multilook (11 x 11 block average) to about 110 m to reduce speckle and resample to a regular lat/lon grid.
4. Convert to dB. Values below -30 dB (sub-swath gaps in the 20 MHz quad-pol mode, far under the noise floor) are dropped before averaging, and a 6-pixel margin around the gaps is masked as no data.
5. Choose one water threshold for the whole season at the valley between the water and land modes of the HH histogram (-17.25 dB here), then clean the mask with a 3x3 majority filter.
6. Compute flooded area per date, change against the first pass, and flood frequency. Write PNG layers plus `manifest.json`.

## Results (central Assam, 2026)
All numbers are in `docs/data/manifest.json`. They cover the 15,255 km2 of the study area that was imaged clearly on all 10 passes.
- About **1,076 km2** was open water on the first pass (25 June 2026), the widest extent of the season.
- Water stayed between roughly **780 and 940 km2** from July to mid-September as repeated monsoon pulses kept the floodplain wet.
- It then drained to **378 km2** by 23 October, **65% less** than in late June.
- **1,391 km2** was under water on at least one pass. **1,138 km2** of that flooded only seasonally, and **254 km2** (mostly the main channel) was water on every pass.

## Run it yourself
```bash
cd pipeline
pip install -r requirements.txt
# Earthdata Login token: https://urs.earthdata.nasa.gov  > Generate Token
# save it to ~/.earthdata_token  (or set EARTHDATA_TOKEN)
python nisar_flood.py --config config.json --out ../docs/data
# or, with .h5 files downloaded from Vertex:
python nisar_flood.py --config config.json --out ../docs/data --local-dir path/to/h5
cd ../docs && python -m http.server 8000   # open http://localhost:8000
```
Windows: double-click `run_pipeline.bat`.

## Data
- NISAR L2 GCOV, L-band, via NASA ASF DAAC: https://search.asf.alaska.edu (granule list in the app's *About & data* panel and in `docs/data/manifest.json`)
- NISAR data user guide: https://nisar-docs.asf.alaska.edu/
- Basemaps: Esri World Imagery, OpenStreetMap contributors

## Limitations
Wind-roughened water and flooded vegetation (which can look bright through double bounce) may be missed. Smooth sand bars and radar shadow can be falsely labelled as water. Numbers are estimates, not official flood statistics.

## Disclaimer
This project is not endorsed by NASA or ISRO.

## Libraries
Leaflet 1.9.4 (BSD-2), Chart.js 4.4.1 (MIT), asf_search, h5py, fsspec, numpy, pyproj, Pillow.

## License
MIT
