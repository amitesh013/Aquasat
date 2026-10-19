import pandas as pd
import numpy as np
from scipy.optimize import curve_fit
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

# -----------------------------------------------------------------
# 1. Load the existing master comparison file (50 rows, 2016-2021)
# -----------------------------------------------------------------
master = pd.read_csv('poondi_final_comparison.csv')
master['date'] = pd.to_datetime(master['image_date'].str[:8], format='%Y%m%d')
master = master.rename(columns={'mcft_exact_date': 'storage_mcft'})
master_slim = master[['date', 'water_area_ha', 'storage_mcft', 'cloud_pct']].copy()
master_slim['source'] = 'monthly_original'

# -----------------------------------------------------------------
# 2. Load the raw drought-scan satellite results (18 candidates)
# -----------------------------------------------------------------
drought = pd.read_csv('poondi_drought_candidates.csv')
drought['date'] = pd.to_datetime(drought['ymd'], format='%Y%m%d')

# -----------------------------------------------------------------
# 3. Storage values manually looked up on CMWSSB for these dates
#    (from cmwssb_lookup_drought_2019_csv.xlsx)
# -----------------------------------------------------------------
storage_lookup = {
    '2019-04-06': 313, '2019-04-11': 288, '2019-04-16': 263, '2019-04-26': 217,
    '2019-05-11': 153, '2019-05-16': 128, '2019-05-21': 104, '2019-05-31': 59,
    '2019-06-05': 46, '2019-06-15': 27, '2019-07-15': 16, '2019-07-20': 16,
    '2019-08-19': 16, '2019-09-13': 15,
}
drought['date_str'] = drought['date'].dt.strftime('%Y-%m-%d')
drought['storage_mcft'] = drought['date_str'].map(storage_lookup)

# Keep only the NEW rows we have a real storage value for, and that
# aren't already covered by the master file (2019-04-01, 05-06, 05-26,
# 08-29 turned out to already be in the master set — no duplicates).
drought_new = drought.dropna(subset=['storage_mcft']).copy()
drought_new = drought_new[['date', 'water_area_ha', 'storage_mcft', 'cloud_pct']]
drought_new['source'] = 'drought_scan_2019'

# -----------------------------------------------------------------
# 4. Combine into one master calibration dataset
# -----------------------------------------------------------------
combined = pd.concat([master_slim, drought_new], ignore_index=True)
combined = combined.sort_values('date').reset_index(drop=True)
combined.to_csv('poondi_master_calibration.csv', index=False)

print(f"Total rows before cloud filter: {len(combined)}")

# -----------------------------------------------------------------
# 5. Filter to usable (cloud <= 25%) rows, same rule as before
# -----------------------------------------------------------------
usable = combined[combined['cloud_pct'] <= 25].copy()
print(f"Usable rows (cloud <= 25%): {len(usable)}  (was 41 before)")

X = usable['water_area_ha'].values.reshape(-1, 1)
y = usable['storage_mcft'].values

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

# -----------------------------------------------------------------
# 6. Refit power-law model: storage = a * area^b
# -----------------------------------------------------------------
def power_law(area, a, b):
    return a * np.power(area, b)

params, _ = curve_fit(power_law, X_train.flatten(), y_train, p0=[1, 1], maxfev=10000)
pred_pl = power_law(X_test.flatten(), *params)
rmse_pl = np.sqrt(mean_squared_error(y_test, pred_pl))

# -----------------------------------------------------------------
# 7. Refit Gaussian Process model
# -----------------------------------------------------------------
kernel = RBF(length_scale=500, length_scale_bounds=(10, 1e5)) + WhiteKernel(noise_level=100)
gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=5, random_state=42)
gp.fit(X_train, y_train)
pred_gp, std_gp = gp.predict(X_test, return_std=True)
rmse_gp = np.sqrt(mean_squared_error(y_test, pred_gp))

print(f"\nPower-law RMSE: {rmse_pl:.1f} mcft  (was ~301 mcft before)")
print(f"Gaussian Process RMSE: {rmse_gp:.1f} mcft  (was ~255 mcft before)")

# -----------------------------------------------------------------
# 8. Specifically check accuracy at LOW storage (< 200 mcft)
#    since that's the project's actual point (drought early-warning)
# -----------------------------------------------------------------
low_mask = y_test < 200
if low_mask.sum() > 0:
    rmse_pl_low = np.sqrt(mean_squared_error(y_test[low_mask], pred_pl[low_mask]))
    rmse_gp_low = np.sqrt(mean_squared_error(y_test[low_mask], pred_gp[low_mask]))
    print(f"\nLow-storage-only test points (n={low_mask.sum()}):")
    print(f"  Power-law RMSE (low storage): {rmse_pl_low:.1f} mcft")
    print(f"  GP RMSE (low storage): {rmse_gp_low:.1f} mcft")
else:
    print("\nNo low-storage points landed in this test split — try a different random_state to check.")

print(f"\nFull test set (area_ha, actual_mcft, power_law_pred, gp_pred):")
for a, actual, pl, gpp in zip(X_test.flatten(), y_test, pred_pl, pred_gp):
    print(f"  area={a:8.1f} ha | actual={actual:7.0f} | power_law={pl:8.1f} | gp={gpp:8.1f}")

# -----------------------------------------------------------------
# 9. Tuned GP: fit in log-space instead of raw mcft space.
#    Raw-space GP assumed roughly constant noise everywhere, which
#    doesn't match reality — errors near empty are much smaller in
#    absolute terms than errors near full. Log-space fixes this the
#    same way power-law's a*area^b form implicitly does, while still
#    keeping GP's uncertainty bounds.
# -----------------------------------------------------------------
X_train_log = np.log1p(X_train)
X_test_log = np.log1p(X_test)
y_train_log = np.log1p(y_train)

kernel_log = RBF(length_scale=1.0, length_scale_bounds=(1e-2, 1e2)) + WhiteKernel(
    noise_level=0.01, noise_level_bounds=(1e-6, 1.0)
)
gp_log = GaussianProcessRegressor(
    kernel=kernel_log, normalize_y=True, n_restarts_optimizer=10, random_state=42
)
gp_log.fit(X_train_log, y_train_log)

pred_gp_log_space, std_gp_log_space = gp_log.predict(X_test_log, return_std=True)
pred_gp_tuned = np.expm1(pred_gp_log_space)  # back to real mcft units

rmse_gp_tuned = np.sqrt(mean_squared_error(y_test, pred_gp_tuned))

print(f"\n--- Tuned (log-space) GP ---")
print(f"Overall RMSE: {rmse_gp_tuned:.1f} mcft  (untuned GP was {rmse_gp:.1f})")

if low_mask.sum() > 0:
    rmse_gp_tuned_low = np.sqrt(mean_squared_error(y_test[low_mask], pred_gp_tuned[low_mask]))
    print(f"Low-storage RMSE: {rmse_gp_tuned_low:.1f} mcft  (untuned GP was {rmse_gp_low:.1f}, power-law was {rmse_pl_low:.1f})")

print(f"\nFull test set with tuned GP added:")
for a, actual, pl, gpp, gpt in zip(X_test.flatten(), y_test, pred_pl, pred_gp, pred_gp_tuned):
    print(f"  area={a:8.1f} ha | actual={actual:7.0f} | power_law={pl:8.1f} | gp_old={gpp:8.1f} | gp_tuned={gpt:8.1f}")

# -----------------------------------------------------------------
# 10. FINAL production power-law model — refit on ALL usable rows
#     (no holdout). The train/test split above was only to honestly
#     measure accuracy (93.6 mcft RMSE); this final version is what
#     you'd actually plug into a dashboard/backend to convert a live
#     area reading into an estimated storage volume.
# -----------------------------------------------------------------
X_all = usable['water_area_ha'].values
y_all = usable['storage_mcft'].values

final_params, _ = curve_fit(power_law, X_all, y_all, p0=[1, 1], maxfev=10000)
a_final, b_final = final_params

print(f"\n--- FINAL production power-law model (fit on all {len(usable)} rows) ---")
print(f"storage_mcft = {a_final:.6f} * area_ha ^ {b_final:.6f}")
print(f"(Reported accuracy for this model type: RMSE ~93.6 mcft, from the held-out test above)")