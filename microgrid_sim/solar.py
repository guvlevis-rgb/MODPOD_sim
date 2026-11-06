"""Solar geometry, irradiance decomposition and plane-of-array transposition.

Pure-numpy implementations so the tool has no pvlib dependency:
  * NOAA solar-position algorithm (Spencer / Michalsky simplified forms)
  * Erbs (1982) diffuse-fraction decomposition of GHI -> DNI + DHI
  * Isotropic-sky (Liu-Jordan) transposition to a tilted plane, with ground reflection
"""
from __future__ import annotations

import numpy as np

DEG = np.pi / 180.0
SOLAR_CONSTANT = 1361.0  # W/m2


def solar_position(day_of_year, hour_mid, lat_deg, lon_deg, tz_hours):
    """Return (zenith_deg, azimuth_deg, extraterrestrial_normal_Wm2).

    day_of_year : 1..365 array
    hour_mid    : local *standard* clock hour at the middle of the interval (e.g. 0.5 for 00:00-01:00)
    Azimuth is measured clockwise from north (180 = due south).
    """
    doy = np.asarray(day_of_year, dtype=float)
    hr = np.asarray(hour_mid, dtype=float)
    gamma = 2 * np.pi / 365.0 * (doy - 1 + (hr - 12) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma)
                       - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
            - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma)
            - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma))
    time_offset = eqtime + 4 * lon_deg - 60 * tz_hours
    tst = hr * 60 + time_offset
    ha = (tst / 4 - 180) * DEG
    lat = lat_deg * DEG
    cosz = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    cosz = np.clip(cosz, -1, 1)
    zen = np.arccos(cosz)
    sinz = np.maximum(np.sin(zen), 1e-6)
    cos_az = (np.sin(decl) - np.sin(lat) * cosz) / (np.cos(lat) * sinz)
    az = np.arccos(np.clip(cos_az, -1, 1))
    az = np.where(ha > 0, 2 * np.pi - az, az)
    e0 = 1 + 0.033 * np.cos(2 * np.pi * doy / 365.0)
    return zen / DEG, az / DEG, SOLAR_CONSTANT * e0


def erbs_decomposition(ghi, zenith_deg, etr_normal):
    """Split GHI into DNI and DHI with the Erbs correlation."""
    cosz = np.cos(zenith_deg * DEG)
    etr_h = np.maximum(etr_normal * cosz, 0)
    kt = np.where(etr_h > 10, ghi / np.maximum(etr_h, 1e-6), 0.0)
    kt = np.clip(kt, 0, 1)
    df = np.where(kt <= 0.22, 1 - 0.09 * kt,
                  np.where(kt <= 0.8,
                           0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4,
                           0.165))
    dhi = ghi * df
    dni = np.where(cosz > 0.087, (ghi - dhi) / np.maximum(cosz, 0.087), 0.0)  # cut below ~5 deg elevation
    dni = np.clip(dni, 0, etr_normal)
    dhi = ghi - dni * np.maximum(cosz, 0)
    return dni, np.maximum(dhi, 0)


def poa_irradiance(ghi, dni, dhi, zenith_deg, azimuth_deg, tilt_deg, surface_az_deg, albedo):
    """Isotropic-sky plane-of-array irradiance (W/m2) and angle of incidence (deg)."""
    zen = zenith_deg * DEG
    tilt = tilt_deg * DEG
    cos_aoi = (np.cos(zen) * np.cos(tilt)
               + np.sin(zen) * np.sin(tilt) * np.cos((azimuth_deg - surface_az_deg) * DEG))
    beam = dni * np.maximum(cos_aoi, 0)
    beam = np.where(zenith_deg < 90, beam, 0)
    sky = dhi * (1 + np.cos(tilt)) / 2
    ground = ghi * albedo * (1 - np.cos(tilt)) / 2
    aoi = np.arccos(np.clip(cos_aoi, -1, 1)) / DEG
    return beam + sky + ground, beam, aoi


def iam_ashrae(aoi_deg, b0=0.05):
    """ASHRAE incidence-angle modifier for glass-covered modules / windows."""
    cos_a = np.cos(np.clip(aoi_deg, 0, 89.9) * DEG)
    iam = 1 - b0 * (1 / cos_a - 1)
    return np.clip(np.where(aoi_deg < 90, iam, 0), 0, 1)
