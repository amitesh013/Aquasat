import pandas as pd
import numpy as np
from scipy.optimize import curve_fit
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# -----------------------------------------------------------------
# Load ground truth
# -----------------------------------------------------------------
gt = pd.read_csv('handwritten_ground_truth_clean.csv')
gt['date'] = pd.to_datetime(gt['date_str'], dayfirst=True)

# -----------------------------------------------------------------
# Drought extra data (collected manually earlier)
# -----------------------------------------------------------------
poondi_drought = pd.DataFrame({
    'date': pd.to_datetime(['20190406','20190411','20190416','20190426',
        '20190511','20190516','20190521','20190531','20190605',
        '20190615','20190715','20190720','20190819','20190913'], format='%Y%m%d'),
    'water_area_hectares': [573.6,475.0,529.2,408.7,342.5,345.0,286.1,
        209.9,180.8,120.1,51.1,51.2,67.6,71.8],
    'storage_mcft': [313,288,263,217,153,128,104,59,46,27,16,16,16,15],
    'cloud_percent': [0.0,4.6,0.0,23.6,0.1,24.6,0.0,5.9,19.8,0.1,12.1,13.4,23.4,5.9],
})

chem_drought = pd.DataFrame({
    'date': pd.to_datetime(['20190401','20190406','20190411','20190416','20190426',
        '20190506','20190511','20190516','20190521','20190526',
        '20190531','20190605','20190615','20190715','20190720',
        '20190819','20190829','20190913'], format='%Y%m%d'),
    'water_area_hectares': [536.6,467.1,215.2,520.6,157.4,105.9,93.0,487.4,
        219.0,317.1,143.7,117.2,3.1,1.8,30.7,90.4,74.5,33.1],
    'storage_mcft': [10,8,6,5,3,2,1,1,1,1,1,1,1,1,0,0,0,0],
    'cloud_percent': [0.0,0.0,4.6,0.0,23.6,5.3,0.1,24.6,0.0,0.0,
        5.9,19.8,0.1,12.1,13.4,23.4,3.4,5.9],
})

drought_extras = {'poondi': poondi_drought, 'chembarambakkam': chem_drought}

# -----------------------------------------------------------------
# Which model wins per reservoir (from earlier comparison)
# GP for poondi + cholavaram, power-law for the rest
# -----------------------------------------------------------------
USE_GP = {'poondi', 'cholavaram'}

def power_law(area, a, b):
    return a * np.power(np.maximum(area, 1e-6), b)

RESERVOIRS = ['poondi', 'cholavaram', 'puzhal', 'chembarambakkam', 'veeranam']
production_models = {}

print(f"{'Reservoir':<20} {'N':>4} {'Model':<12} {'RMSE (mcft)':>12}")
print("-" * 52)

for name in RESERVOIRS:
    # Load satellite CSV
    sat = pd.read_csv(f'{name}_satellite_areas.csv')
    sat['date'] = pd.to_datetime(sat['image_ymd'].astype(str), format='%Y%m%d')

    # Ground truth for this reservoir
    gt_res = gt[['date', name]].dropna(subset=[name]).rename(
        columns={name: 'storage_mcft'})

    # Merge on exact date
    merged = pd.merge(
        sat[['date', 'cloud_percent', 'water_area_hectares']],
        gt_res, on='date', how='inner'
    )
    merged = merged[merged['cloud_percent'] <= 25].copy()

    # Add drought extras if available
    if name in drought_extras:
        extra = drought_extras[name][drought_extras[name]['cloud_percent'] <= 25]
        merged = pd.concat([merged, extra], ignore_index=True)

    X = merged['water_area_hectares'].values
    y = merged['storage_mcft'].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42)

    if name in USE_GP:
        # Gaussian Process in log space
        X_tr = np.log1p(X_train).reshape(-1, 1)
        X_te = np.log1p(X_test).reshape(-1, 1)
        y_tr = np.log1p(y_train)

        kernel = RBF(length_scale=1.0, length_scale_bounds=(1e-2, 1e2)) + \
                 WhiteKernel(noise_level=0.1, noise_level_bounds=(1e-6, 1.0))
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                       n_restarts_optimizer=10, random_state=42)
        gp.fit(X_tr, y_tr)

        pred_log, _ = gp.predict(X_te, return_std=True)
        pred = np.expm1(pred_log)
        rmse = np.sqrt(mean_squared_error(y_test, pred))

        # Refit on ALL data for production use
        gp.fit(np.log1p(X).reshape(-1, 1), np.log1p(y))
        production_models[name] = {'type': 'gp', 'model': gp}
        model_label = 'Gaussian Process'

    else:
        # Power-law
        params, _ = curve_fit(power_law, X_train, y_train, p0=[1,1], maxfev=10000)
        pred = power_law(X_test, *params)
        rmse = np.sqrt(mean_squared_error(y_test, pred))

        # Refit on ALL data for production use
        params_all, _ = curve_fit(power_law, X, y, p0=[1,1], maxfev=10000)
        a, b = params_all
        production_models[name] = {'type': 'power_law', 'a': a, 'b': b}
        model_label = 'Power-law'

    print(f"{name:<20} {len(merged):>4} {model_label:<12} {rmse:>12.1f}")

# -----------------------------------------------------------------
# Print production formulas for power-law reservoirs
# (GP models are saved as objects — use them directly in code)
# -----------------------------------------------------------------
print("\n--- Production formulas (power-law reservoirs) ---")
for name, m in production_models.items():
    if m['type'] == 'power_law':
        print(f"{name}: storage_mcft = {m['a']:.6f} * area_ha ^ {m['b']:.6f}")

print("\nDone! Models ready to use for live storage estimation.")