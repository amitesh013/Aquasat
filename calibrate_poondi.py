import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel
from sklearn.metrics import mean_squared_error

INPUT_CSV = 'poondi_final_comparison.csv'
CLOUD_THRESHOLD = 25
RANDOM_STATE = 42
TEST_SIZE = 0.2

df = pd.read_csv(INPUT_CSV)
clean = df[df['cloud_pct'] <= CLOUD_THRESHOLD].copy()
clean = clean[(clean['water_area_ha'] > 0) & (clean['mcft_exact_date'] > 0)]

print(f"Total rows: {len(df)}")
print(f"Rows after cloud filter (<= {CLOUD_THRESHOLD}%): {len(clean)}\n")

X = clean['water_area_ha'].values.reshape(-1, 1)
y = clean['mcft_exact_date'].values

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE
)

x_scaler = StandardScaler()
X_train_s = x_scaler.fit_transform(X_train)
X_test_s = x_scaler.transform(X_test)

y_scaler = StandardScaler()
y_train_s = y_scaler.fit_transform(y_train.reshape(-1, 1)).flatten()

kernel = ConstantKernel(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2)) + WhiteKernel(1.0, (1e-3, 1e2))

gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=10, random_state=RANDOM_STATE)
gp.fit(X_train_s, y_train_s)

y_pred_s, y_std_s = gp.predict(X_test_s, return_std=True)
y_pred = y_scaler.inverse_transform(y_pred_s.reshape(-1, 1)).flatten()
y_std = y_std_s * y_scaler.scale_[0]

rmse = np.sqrt(mean_squared_error(y_test, y_pred))
bias = np.mean(y_pred - y_test)

print(f"Fitted kernel: {gp.kernel_}\n")
print(f"Holdout set size: {len(y_test)}")
print(f"RMSE (mcft): {rmse:.1f}")
print(f"Bias (mcft, mean predicted - actual): {bias:.1f}\n")

print("Holdout predictions vs actual (with 95% interval):")
for area, actual, pred, std in zip(X_test.flatten(), y_test, y_pred, y_std):
    lower = pred - 1.96 * std
    upper = pred + 1.96 * std
    print(f"  area {area:8.1f} ha -> predicted {pred:7.1f} mcft "
          f"[{lower:7.1f}, {upper:7.1f}], actual {actual:7.1f} mcft, diff {pred - actual:+7.1f}")

all_X_s = x_scaler.transform(X)
all_pred_s, all_std_s = gp.predict(all_X_s, return_std=True)
all_pred = y_scaler.inverse_transform(all_pred_s.reshape(-1, 1)).flatten()
all_std = all_std_s * y_scaler.scale_[0]

clean['predicted_mcft_gp'] = all_pred
clean['predicted_std_gp'] = all_std
clean['residual_mcft_gp'] = clean['predicted_mcft_gp'] - clean['mcft_exact_date']
clean.to_csv('poondi_calibration_fit_gp.csv', index=False)
print(f"\nSaved full fitted dataset to poondi_calibration_fit_gp.csv")
print(f"\n--- Comparison ---")
print(f"Power-law RMSE (mcft): 301.1")
print(f"GP RMSE (mcft):        {rmse:.1f}")