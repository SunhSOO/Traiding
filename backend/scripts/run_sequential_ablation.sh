#!/bin/bash
# Sequential ablation runner — runs LGBM training for 1.5y, 5y, 10y windows in order
# After ablation: runs EDA, then v3 trainer (GP/BNN/Stacking)
# Each step waits for the previous to finish.

set -e
cd "c:/Users/dksdn/OneDrive/바탕 화면/trading/main-branch/backend"

echo "===  STEP 1: Ablation US 1.5y ($(date)) ==="
uv run python scripts/train_lgbm.py --market US --days 540 \
    --out var/ablation_us_1.5y.json 2>&1 | tail -100 > var/seq_ablation_1.5y.log
echo "===  STEP 1 DONE ($(date)) ==="

echo "===  STEP 2: Ablation US 5y ($(date)) ==="
uv run python scripts/train_lgbm.py --market US --days 1820 \
    --out var/ablation_us_5y.json 2>&1 | tail -100 > var/seq_ablation_5y.log
echo "===  STEP 2 DONE ($(date)) ==="

echo "===  STEP 3: Ablation US 10y ($(date)) ==="
uv run python scripts/train_lgbm.py --market US --days 3650 \
    --out var/ablation_us_10y.json 2>&1 | tail -100 > var/seq_ablation_10y.log
echo "===  STEP 3 DONE ($(date)) ==="

echo "===  STEP 4: EDA Report ($(date)) ==="
uv run python scripts/eda_data_explore.py 2>&1 | tail -100 > var/seq_eda.log
echo "===  STEP 4 DONE ($(date)) ==="

echo "===  STEP 5: v3 Trainer (GP/BNN/Stacking) ($(date)) ==="
uv run python scripts/train_models_v3.py --market US --days 3650 \
    --models gp,bnn,stacking --min-samples 1000 \
    --out-dir var/models_v3 2>&1 | tail -100 > var/seq_v3_train.log
echo "===  STEP 5 DONE ($(date)) ==="

echo "===  ALL SEQUENTIAL DONE ($(date)) ==="
