"""
NISAR GCOV flood-change pipeline.

Finds NISAR L-band L2 GCOV granules over a bounding box, reads only the
pixels it needs (local .h5 files or streamed over HTTPS with an Earthdata
Login bearer token), resamples them to a regular lat/lon grid and writes
web-ready PNG layers plus a manifest.json that the web app reads.

Water detection: open water is a smooth surface that reflects the radar
pulse away from the sensor, so it is very dark in co-polarised (HH)
backscatter. Pixels below a threshold (the valley between the water and land modes
of the HH dB histogram, or a fixed value from config) are labelled water.

Usage:
  # search ASF and stream (needs EDL token in env var EARTHDATA_TOKEN)
  python nisar_flood.py --config config.json --out ../docs/data
  # or use .h5 files you already downloaded from Vertex
  python nisar_flood.py --config config.json --out ../docs/data --local-dir D:/nisar_h5
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import h5py
from PIL import Image
from pyproj import Transformer

TS_RE = re.compile(r"_(\d{8}T\d{6})_")
NODATA_LIN = 10 ** (-30 / 10)  # -30 dB


# ---------------------------------------------------------------- search
FN_RE = re.compile(r"GCOV_(\d{3})_(\d{3})_([AD])_(\d{3})_.*_(\d{8})T\d{6}_.*_([A-Z])_J_(\d{3})\.h5$")


def parse_name(name):
    """cycle, track, direction, frame, date, version from a NISAR GCOV file name."""
    m = FN_RE.search(os.path.basename(name))
    if not m:
        return None
    return dict(cycle=m.group(1), track=m.group(2), dir=m.group(3), frame=m.group(4),
                date=m.group(5), version=int(m.group(7)))


def search_granules(cfg):
    import asf_search as asf

    lon0, lat0, lon1, lat1 = cfg["bbox"]
    wkt = f"POLYGON(({lon0} {lat0},{lon1} {lat0},{lon1} {lat1},{lon0} {lat1},{lon0} {lat0}))"
    opts = dict(dataset=["NISAR"], processingLevel=["GCOV"], intersectsWith=wkt,
                start=cfg["start"], end=cfg["end"], maxResults=1000)
    results = asf.search(**opts)
    urls = results.find_urls(extension=".h5", pattern=r"^(?!.*QA_STATS).*", directAccess=False)
    return select_granules(urls, cfg)


def select_granules(urls, cfg):
    """Keep one repeat-pass track/frame (same viewing geometry on every date),
    drop BETA and urgent-response products and keep the newest version per date."""
    best = {}
    for u in sorted(set(urls)):
        name = os.path.basename(u)
        if "BETA" in u.upper() or "_UR_" in name:
            continue
        info = parse_name(name)
        if not info:
            continue
        if cfg.get("track") and info["track"] != cfg["track"]:
            continue
        if cfg.get("direction") and info["dir"] != cfg["direction"]:
            continue
        if cfg.get("frame") and info["frame"] != cfg["frame"]:
            continue
        key = info["date"]
        if key not in best or info["version"] > parse_name(best[key])["version"]:
            best[key] = u
    return [best[k] for k in sorted(best)]


def list_local(local_dir, cfg):
    files = [str(p) for p in Path(local_dir).rglob("*.h5") if "QA_STATS" not in p.name]
    return select_granules(files, cfg)


def granule_date(name):
    m = TS_RE.search(os.path.basename(name))
    return m.group(1)[:8] if m else None


# ---------------------------------------------------------------- reading
def resolve_url(src, token):
    """Follow the Earthdata redirect once and return the signed download URL,
    so every byte-range request afterwards goes straight to the file host."""
    import requests

    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    url = src
    for _ in range(5):
        r = s.get(url, allow_redirects=False, headers={"Range": "bytes=0-0"}, timeout=60)
        if r.status_code in (301, 302, 303, 307, 308):
            url = r.headers["Location"]
            continue
        r.raise_for_status()
        return url
    raise RuntimeError("too many redirects")


def open_h5(src, token):
    if src.startswith("http"):
        import fsspec

        url = resolve_url(src, token)
        fs = fsspec.filesystem("http", block_size=8 * 1024 * 1024, client_kwargs={"trust_env": True})
        return h5py.File(fs.open(url, "rb"), "r")
    return h5py.File(src, "r")


def read_window(f, freq, pol, lon_grid, lat_grid, band_rows=2048):
    """Sample one polarisation of a GCOV granule onto the target lat/lon grid.

    The window covering the study area is read in horizontal bands, and each
    band is multilooked (k x k block average, k = output pixel / native pixel)
    straight away, so memory stays small and radar speckle is suppressed
    before the water threshold is applied."""
    g = f[f"/science/LSAR/GCOV/grids/{freq}"]
    if pol not in g:
        return None
    x = g["xCoordinates"][:]
    y = g["yCoordinates"][:]
    epsg = int(np.asarray(g["projection"].attrs["epsg_code"]).item())
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    gx, gy = tr.transform(lon_grid, lat_grid)

    dx = x[1] - x[0]
    dy = y[1] - y[0]
    col = np.floor((gx - x[0]) / dx).astype(np.int64)
    row = np.floor((gy - y[0]) / dy).astype(np.int64)
    inside = (col >= 0) & (col < x.size) & (row >= 0) & (row < y.size)
    if not inside.any():
        return None

    k = max(1, int(round(target_pixel_m(lat_grid, lon_grid) / abs(dx))))
    r0, r1 = row[inside].min(), row[inside].max() + 1
    c0, c1 = col[inside].min(), col[inside].max() + 1
    r1 = min(y.size, r0 + -(-(r1 - r0) // k) * k)
    c1 = min(x.size, c0 + -(-(c1 - c0) // k) * k)
    nr, nc = (r1 - r0) // k, (c1 - c0) // k
    looks = np.full((max(nr, 1), max(nc, 1)), np.nan, np.float32)
    step = max(k, (band_rows // k) * k)
    for a in range(r0, r0 + nr * k, step):
        b = min(a + step, r0 + nr * k)
        blk = g[pol][a:b, c0:c0 + nc * k].astype(np.float32)
        # values far below the noise floor are sub-swath gaps / fill, not signal:
        # drop them before averaging so they do not darken neighbouring pixels
        blk[(blk <= NODATA_LIN) | ~np.isfinite(blk)] = np.nan
        with np.errstate(invalid="ignore"):
            ml = np.nanmean(blk.reshape((b - a) // k, k, nc, k), axis=(1, 3))
        looks[(a - r0) // k:(b - r0) // k] = ml

    rr = np.clip((row[inside] - r0) // k, 0, looks.shape[0] - 1)
    cc = np.clip((col[inside] - c0) // k, 0, looks.shape[1] - 1)
    out = np.full(lon_grid.shape, np.nan, np.float32)
    out[inside] = looks[rr, cc]
    out[(out <= 0) | ~np.isfinite(out)] = np.nan
    return out


def read_task(args):
    """Worker: read one (granule, polarisation) pair. Runs in its own process."""
    src, pol, freq, lon_grid, lat_grid, token, cache_dir = args
    cache = Path(cache_dir) / f"{os.path.basename(src)}.{pol}.npy" if cache_dir else None
    if cache and cache.exists():
        return src, pol, np.load(cache)
    for attempt in range(3):
        try:
            with open_h5(src, token) as f:
                arr = read_window(f, freq, pol, lon_grid, lat_grid)
            if cache is not None and arr is not None:
                np.save(cache, arr)
            return src, pol, arr
        except Exception as e:  # network hiccups: retry with a fresh signed URL
            err = e
    print(f"  failed {os.path.basename(src)} {pol}: {err}", flush=True)
    return src, pol, None


def target_pixel_m(lat_grid, lon_grid):
    dlat = abs(lat_grid[1, 0] - lat_grid[0, 0]) if lat_grid.shape[0] > 1 else 0.001
    return dlat * 111320.0


# ---------------------------------------------------------------- analysis
def dilate(mask, r):
    out = mask.copy()
    for _ in range(r):
        p = np.pad(out, 1)
        out = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:])
    return out


def majority3(mask):
    """3x3 majority filter: removes isolated speckle pixels from the water mask."""
    m = mask.astype(np.uint8)
    p = np.pad(m, 1)
    s = sum(p[i:i + m.shape[0], j:j + m.shape[1]] for i in range(3) for j in range(3))
    return s >= 5


def to_db(lin):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(lin)


def otsu(values, bins=256):
    v = values[np.isfinite(values)]
    hist, edges = np.histogram(v, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = (np.sum(hist * centers) - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[np.argmax(between)])


def valley_threshold(values, lo=-26.0, hi=-8.0, step=0.5):
    """Water/land threshold at the histogram valley just above the dark water peak.

    L-band HH over this floodplain is bimodal: calm water sits near the noise
    floor (about -20 dB) and land spreads from -16 dB upwards. Otsu's method
    is pulled towards the much larger land mode, so the valley between the two
    modes is a better split. Falls back to Otsu if no clear valley exists."""
    v = values[np.isfinite(values)]
    hist, edges = np.histogram(v, bins=np.arange(lo, hi + step, step))
    sm = np.convolve(hist, np.ones(3) / 3, mode="same")
    peaks = [i for i in range(1, len(sm) - 1) if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > 0.02 * sm.max()]
    if not peaks:
        return otsu(v)
    p0 = peaks[0]
    rest = sm[p0 + 1:]
    if rest.size == 0:
        return otsu(v)
    j = p0 + 1 + int(np.argmin(rest[: max(1, int(8 / step))]))
    if sm[j] > 0.8 * sm[p0]:
        return otsu(v)
    return float(edges[j] + step / 2)


def stretch(db, lo, hi):
    return np.clip((db - lo) / (hi - lo) * 255, 0, 255)


def save_rgba(path, rgb, valid):
    h, w = valid.shape
    a = np.where(valid, 255, 0).astype(np.uint8)
    rgb = np.nan_to_num(np.asarray(rgb, dtype=np.float64), nan=0.0)
    arr = np.dstack([rgb[..., 0], rgb[..., 1], rgb[..., 2], a]).astype(np.uint8)
    Image.fromarray(arr, "RGBA").save(path, optimize=True)


def pixel_area_km2(lat_grid, pixel_deg):
    km_per_deg = 111.32
    return (pixel_deg * km_per_deg) ** 2 * np.cos(np.radians(lat_grid))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="../docs/data")
    ap.add_argument("--local-dir", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cache-dir", default=None, help="keep per-granule arrays here so reruns are fast")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    out = Path(args.out)
    (out / "layers").mkdir(parents=True, exist_ok=True)
    token = os.environ.get("EARTHDATA_TOKEN", "")
    tok_file = Path.home() / ".earthdata_token"
    if not token and tok_file.exists():
        token = tok_file.read_text().strip()

    if args.local_dir:
        sources = list_local(args.local_dir, cfg)
    else:
        if not token:
            sys.exit("Set EARTHDATA_TOKEN (Earthdata Login > Generate Token) or use --local-dir.")
        sources = search_granules(cfg)
    print(f"{len(sources)} granules selected", flush=True)

    by_date = defaultdict(list)
    for s_ in sources:
        d = granule_date(s_)
        if d:
            by_date[d].append(s_)
    dates = sorted(by_date)
    if cfg.get("max_dates") and len(dates) > cfg["max_dates"]:
        idx = np.linspace(0, len(dates) - 1, cfg["max_dates"]).round().astype(int)
        dates = [dates[i] for i in sorted(set(idx))]

    lon0, lat0, lon1, lat1 = cfg["bbox"]
    p = cfg["pixel_deg"]
    lons = np.arange(lon0 + p / 2, lon1, p)
    lats = np.arange(lat1 - p / 2, lat0, -p)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    area = pixel_area_km2(lat_grid, p)
    freq = cfg.get("frequency", "frequencyA")

    cache_dir = args.cache_dir
    if cache_dir:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
    tasks = [(src, pol, freq, lon_grid, lat_grid, token, cache_dir)
             for d in dates for src in by_date[d] for pol in ("HHHH", "HVHV")]
    results = {}
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(read_task, t) for t in tasks]
        for i, fu in enumerate(as_completed(futs), 1):
            src, pol, arr = fu.result()
            results[(src, pol)] = arr
            print(f"  [{i}/{len(tasks)}] {os.path.basename(src)[:60]} {pol}", flush=True)

    stacks = {}
    for d in dates:
        hh = np.full(lon_grid.shape, np.nan, np.float32)
        hv = np.full(lon_grid.shape, np.nan, np.float32)
        used = []
        for src in by_date[d]:
            a_ = results.get((src, "HHHH"))
            b_ = results.get((src, "HVHV"))
            if a_ is None:
                continue
            fill = np.isnan(hh) & np.isfinite(a_)
            hh[fill] = a_[fill]
            if b_ is not None:
                hv[fill] = b_[fill]
            used.append(os.path.basename(src))
        cover = np.isfinite(hh).mean()
        print(f"{d}: {len(used)} granules, coverage {cover:.0%}", flush=True)
        if cover < 0.2:
            continue
        hh_db, hv_db = to_db(hh), to_db(hv)
        # Sub-swath gaps (seen as dark stripes in some acquisition modes) sit far
        # below the radar noise floor. Treat them, plus a 2-pixel margin, as no data
        # so they are not mistaken for water.
        gap = np.isfinite(hh_db) & (hh_db < cfg.get("nodata_below_db", -27.0))
        # the gap edges are smeared over a few pixels, so widen the no-data zone
        gap = dilate(gap | ~np.isfinite(hh_db), cfg.get("gap_margin_px", 6))
        hh_db[gap] = np.nan
        hv_db[gap] = np.nan
        stacks[d] = dict(hh=hh_db, hv=hv_db, granules=used)

    if not stacks:
        sys.exit("No usable dates over the study area.")

    # one water threshold for the whole series so dates are comparable
    thr = cfg.get("water_threshold_db")
    if thr is None:
        allhh = np.concatenate([s["hh"][np.isfinite(s["hh"])][::7] for s in stacks.values()])
        thr = valley_threshold(allhh)
    print(f"water threshold HH = {thr:.1f} dB")

    dlist = sorted(stacks)
    base = dlist[0]
    common = np.logical_and.reduce([np.isfinite(stacks[d]["hh"]) for d in dlist])
    water = {d: majority3((stacks[d]["hh"] < thr) & np.isfinite(stacks[d]["hh"])) & np.isfinite(stacks[d]["hh"])
             for d in dlist}
    ever = np.logical_or.reduce([water[d] & common for d in dlist])
    always = np.logical_and.reduce([water[d] | ~common for d in dlist]) & common
    freq_map = np.sum([water[d] & common for d in dlist], axis=0) / len(dlist)

    manifest = dict(
        study_area=cfg["study_area"],
        bbox=cfg["bbox"],
        bounds=[[lat0, lon0], [lat1, lon1]],
        threshold_db=round(thr, 2),
        pixel_deg=p,
        baseline=base,
        dates=[],
    )

    for d in dlist:
        s = stacks[d]
        hh, hv = s["hh"], s["hv"]
        valid = np.isfinite(hh)
        # grey HH backscatter
        g = stretch(hh, -25, 0)
        save_rgba(out / "layers" / f"{d}_hh.png", np.dstack([g, g, g]), valid)
        # false colour: R=HH, G=HV, B=HH/HV ratio  (vegetation green-ish, water black, urban pink)
        hvv = np.where(np.isfinite(hv), hv, hh - 8)
        rgb = np.dstack([stretch(hh, -25, 0), stretch(hvv, -32, -8), stretch(hh - hvv, 0, 15)])
        save_rgba(out / "layers" / f"{d}_rgb.png", rgb, valid)
        # water mask (blue)
        w = water[d]
        col = np.zeros(hh.shape + (3,))
        col[w] = (30, 120, 255)
        save_rgba(out / "layers" / f"{d}_water.png", col, w)
        # change vs baseline: new water (red), receded water (yellow), persistent (blue)
        wb = water[base]
        both = valid & np.isfinite(stacks[base]["hh"])
        new = w & ~wb & both
        gone = ~w & wb & both
        keep = w & wb & both
        col = np.zeros(hh.shape + (3,))
        col[new] = (230, 40, 40)
        col[gone] = (255, 200, 0)
        col[keep] = (30, 90, 200)
        save_rgba(out / "layers" / f"{d}_change.png", col, new | gone | keep)

        manifest["dates"].append(dict(
            date=f"{d[:4]}-{d[4:6]}-{d[6:]}",
            id=d,
            granules=s["granules"],
            coverage=round(float(valid.mean()), 3),
            water_km2=round(float(area[w & common].sum()), 1),
            new_water_km2=round(float(area[new & common].sum()), 1),
            receded_km2=round(float(area[gone & common].sum()), 1),
            mean_hh_db=round(float(np.nanmean(hh[common])), 2),
            mean_hv_db=round(float(np.nanmean(hv[common])), 2) if np.isfinite(hv[common]).any() else None,
        ))

    # flood frequency layer: how often each pixel was water across the season
    col = np.zeros(freq_map.shape + (3,))
    t = np.clip(freq_map, 0, 1)
    col[..., 0] = 255 * (1 - t)
    col[..., 1] = 255 * (1 - t * 0.6)
    col[..., 2] = 255
    save_rgba(out / "layers" / "flood_frequency.png", col, ever)
    manifest["summary"] = dict(
        permanent_water_km2=round(float(area[always].sum()), 1),
        ever_flooded_km2=round(float(area[ever].sum()), 1),
        seasonal_only_km2=round(float(area[ever & ~always].sum()), 1),
        common_area_km2=round(float(area[common].sum()), 1),
        peak_date=max(manifest["dates"], key=lambda r: r["water_km2"])["date"],
        peak_water_km2=max(r["water_km2"] for r in manifest["dates"]),
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest["summary"], indent=1))


if __name__ == "__main__":
    main()
