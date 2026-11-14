@echo off
REM Builds the NISAR flood layers for the web app (Windows).
REM Put your Earthdata Login token (urs.earthdata.nasa.gov > Generate Token)
REM in a file named .earthdata_token in your user folder, e.g. C:\Users\you\.earthdata_token
cd /d "%~dp0pipeline"
python -m pip install -r requirements.txt
python nisar_flood.py --config config.json --out ..\docs\data
echo Done. Open docs\index.html through a local server: cd docs ^&^& python -m http.server
pause
