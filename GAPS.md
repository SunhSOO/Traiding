# GAPS — 미실현/미구현 전수 목록

> **이 문서의 목적**: "전부 시도" 원칙을 지키기 위한 영구 산출물.
> 작업하면서 발견되는 누락은 *즉시* 이 문서에 append.
> 채팅/회의에서만 논의하지 말 것. 휘발됨.
>
> **상태 표기**:
> - ⬜ TODO — 미구현
> - 🟦 WIP — 구현 진행 중 (background)
> - ✅ DONE — 구현 + 측정 완료
> - ⏭️ EXCLUDED — 측정 결과 효용 없음 (이유 기록)
> - ⛔ BLOCKED — 외부 의존성 부재 (이유 기록)

---

## A. 데이터 / 피처

### A.1 Technical / Price (현재 ~50, 가능 200+)

**Returns**:
- ✅ ret_1d, 2d, 3d, 5d, 10d, 21d, 42d, 63d, 126d, 252d
- ⬜ ret_7d, 14d, 30d, 60d, 90d, 180d, 504d
- ⬜ 비표준 윈도우 (22d US 거래일, 20d KR)
- ⬜ Returns adjusted for ex-dividend (currently use adj_close)

**Volatility**:
- ✅ vol_5d, 21d, 63d, 252d (realized)
- ⬜ vol_10d, 30d, 60d, 90d, 126d
- ⬜ Volatility ratios (5d/63d, 21d/252d)
- ⬜ GARCH(1,1) conditional vol
- ⬜ EWMA vol (λ=0.94)
- ⬜ **Garman-Klass** vol (OHLC 활용)
- ⬜ **Parkinson** vol
- ⬜ **Rogers-Satchell** vol
- ⬜ **Yang-Zhang** vol (overnight + intraday)
- ⬜ Vol clustering measure (ARCH effect)
- ⬜ Vol skew (left vs right tail)
- ⬜ Vol-of-vol

**Drawdown / Trend**:
- ✅ dd_from_high_63d, dd_from_high_252d
- ⬜ Days since 52-week high/low
- ⬜ Days in current trend
- ⬜ Trend strength (R² of price vs linear regression)
- ⬜ Hurst exponent (mean-reverting vs trending)
- ⬜ Detrended Price Oscillator

**Volume**:
- ✅ volume_z21, volume_z63, obv_slope21
- ⬜ volume_z 5d, 252d
- ⬜ Volume trend slope
- ⬜ Accumulation/Distribution Line slope
- ⬜ Force Index (Elder)
- ⬜ Ease of Movement (Arms)
- ⬜ Chaikin Oscillator
- ⬜ Klinger Volume Oscillator
- ⬜ Volume Price Trend (VPT)
- ⬜ Negative Volume Index (NVI), Positive (PVI)
- ⬜ Volume Profile (Point of Control)
- ⬜ VWAP deviation
- ⬜ Dollar volume z-score
- ⬜ Volume regime (z relative to ticker history)

**Momentum/Oscillators**:
- ✅ RSI 5/14, MACD-hist/signal, ADX 14, Stoch K 14, Williams %R 14, MFI 14, CMF 21, Aroon up/dn/osc, ROC 10/21
- ⬜ RSI 28, 50
- ⬜ Stoch D (slow %D)
- ⬜ Stoch RSI
- ⬜ Connor's RSI (3-period composite)
- ⬜ Ultimate Oscillator
- ⬜ TRIX (triple smoothed)
- ⬜ KST (Know Sure Thing)
- ⬜ DPO (Detrended Price Oscillator)
- ⬜ TSI (True Strength Index)
- ⬜ PPO (Percentage Price Oscillator)
- ⬜ PVO (Percentage Volume Oscillator)
- ⬜ BOP (Balance of Power)
- ⬜ Chande Momentum Oscillator
- ⬜ DMI Plus / Minus separately
- ⬜ Vortex Indicator (VI+/VI-)
- ⬜ Mass Index
- ⬜ Coppock Curve
- ⬜ Fractal Adaptive Moving Average

**Trend / Moving Averages**:
- ✅ vs SMA 20/50/200, SMA50_above_SMA200
- ⬜ vs SMA 10, 100, 400
- ⬜ EMA 12, 26, 50, 200
- ⬜ DEMA, TEMA, TRIMA, KAMA, MAMA, T3
- ⬜ WMA, Hull MA (HMA), ALMA, McGinley, ZeroLag EMA, VIDYA
- ⬜ Linear Regression Line / slope
- ⬜ MA crossover signals (golden/death cross flags)
- ⬜ Distance from MA in ATR units
- ⬜ Parabolic SAR with current direction
- ⬜ SuperTrend

**Cycle / Hilbert**:
- ⬜ Hilbert HT_DCPERIOD, HT_DCPHASE, HT_PHASOR, HT_SINE, HT_TRENDMODE
- ⬜ Schaff Trend Cycle
- ⬜ Ehlers Cycle indicators

**Volatility Bands**:
- ✅ Bollinger %B + squeeze
- ⬜ Bollinger width trend
- ⬜ Keltner Channels position
- ⬜ Donchian channel width

**Candlestick Patterns** (TA-Lib has 61, 우리 4개):
- ✅ doji, body_pct, upper/lower_wick_pct
- ⬜ Engulfing (bullish/bearish)
- ⬜ Harami / Harami Cross
- ⬜ Hammer / Hanging Man / Inverted Hammer
- ⬜ Shooting Star / Morning Star / Evening Star
- ⬜ Three White Soldiers / Three Black Crows
- ⬜ Dark Cloud Cover / Piercing Pattern
- ⬜ Marubozu (long/short, white/black)
- ⬜ Spinning Top
- ⬜ Three Inside Up/Down, Three Outside Up/Down
- ⬜ Belt Hold / Counterattack / Tasuki Gap
- ⬜ Mat Hold / Concealing Baby Swallow
- ⬜ Stick Sandwich, Stalled Pattern
- ⬜ (TA-Lib 60+ 패턴 모두)

**Statistical**:
- ✅ skew_21d, kurt_21d, sharpe_21d/63d
- ⬜ Skew / Kurt at 63d, 126d, 252d
- ⬜ Sharpe at 5d, 10d, 252d
- ⬜ Sortino (downside vol)
- ⬜ Calmar
- ⬜ Information ratio
- ⬜ Treynor (need beta)
- ⬜ **Rolling Beta** to market (5d, 21d, 63d, 252d)
- ⬜ Alpha (CAPM residual)
- ⬜ Tracking error vs benchmark
- ⬜ Active return
- ⬜ Pearson/Spearman corr with market
- ⬜ Autocorrelation (1, 5, 21 lags)
- ⬜ VaR(5%), VaR(1%), CVaR
- ⬜ Drawdown duration & recovery time
- ⬜ Pain Index

### A.2 Fundamental (현재 ~10, 가능 50+)

**Valuation**:
- ✅ pe_ttm, pb, ev_ebitda
- ⬜ P/S (price/sales)
- ⬜ P/CF (price/cash flow)
- ⬜ EV/Revenue, EV/FCF
- ⬜ PEG (P/E to growth)
- ⬜ Dividend Yield, Earnings Yield (1/PE), FCF Yield
- ⬜ Shareholder Yield (div + buybacks)
- ⬜ Forward P/E (if analyst data available)

**Profitability**:
- ✅ roe_q, roa_q, gross_margin
- ⬜ ROIC (Return on Invested Capital)
- ⬜ ROCE (Return on Capital Employed)
- ⬜ Operating Margin, Net Margin, EBITDA Margin, FCF Margin
- ⬜ Gross Profitability (Novy-Marx)

**Growth**:
- ✅ rev_yoy, eps_yoy
- ⬜ Revenue QoQ, EPS QoQ
- ⬜ Revenue CAGR (3yr, 5yr), EPS CAGR
- ⬜ Margin expansion
- ⬜ Revenue / EPS growth acceleration
- ⬜ Book value growth
- ⬜ Dividend growth rate

**Quality / Balance Sheet**:
- ✅ current_ratio, debt_equity
- ⬜ Quick Ratio, Cash Ratio
- ⬜ Total Debt/Total Capital
- ⬜ Debt/EBITDA
- ⬜ Interest Coverage, Times Interest Earned
- ⬜ Working Capital / Sales
- ⬜ Asset Turnover, Inventory Turnover, Receivables Turnover
- ⬜ Days Sales/Inventory/Payable Outstanding
- ⬜ Cash Conversion Cycle (CCC)
- ⬜ Goodwill / Total Assets

**Cash Flow Quality**:
- ⬜ CFO/Net Income (Sloan ratio)
- ⬜ FCF / Net Income
- ⬜ Capex / Revenue, Capex / Depreciation
- ⬜ FCF stability (3yr std)

**Composite Scores**:
- ⬜ **Piotroski F-Score** (9 binary bits)
- ⬜ **Altman Z-Score** (부도위험)
- ⬜ **Beneish M-Score** (이익조작 탐지)
- ⬜ Magic Formula (Greenblatt)
- ⬜ Mohanram G-Score

**Sector-relative percentile**:
- ⬜ 위 모든 메트릭의 sector 백분위 rank (현재 composite 하나만)

**Earnings timing**:
- ⬜ Days since earnings
- ⬜ Days to next earnings
- ⬜ Earnings surprise (actual vs consensus or trailing 4Q)

### A.3 Information / News (현재 ~6, 가능 30+)

- ✅ news_count_7d/30d, sentiment_7d, pos/neg counts, impact
- ⬜ News count: 1d, 14d, 60d, 90d, 180d
- ⬜ Sentiment: 1d, 14d, 30d, 90d
- ⬜ Sentiment momentum (delta vs 30d)
- ⬜ Sentiment volatility
- ⬜ News count 백분위 (해당 ticker 자체 historical)
- ⬜ Publisher diversity (Shannon entropy)
- ⬜ Publisher trust weighted score
- ⬜ Topic-specific counts (M&A, earnings, lawsuit, dividend, layoff)
- ⬜ Article length distribution
- ⬜ **GDELT V2Tone direct extraction** (현재 summary에만 저장!)
- ⬜ **GDELT V2Themes** (M&A/earnings/lawsuit 코드 직접)
- ⬜ GDELT V2Locations
- ⬜ GDELT V2Persons (CEO 언급)
- ⬜ GDELT V2Organizations co-mention
- ⬜ Source country diversity
- ⬜ LLM classification (1.4M GDELT 미분류, sample-based)
- ⬜ Co-mention network (A-B 동시 언급)
- ⬜ Mention timing (pre-market vs after-hours)
- ⬜ Weekend news count
- ⬜ Mention rank vs sector peers

### A.4 Disclosure (현재 ~13, 가능 30+)

- ✅ insider count 7d/30d, 8K count 7d/30d, days since 10K/Q
- ✅ Form 4: net_value, buys/sells, CEO/Director, buy_sell_ratio
- ⬜ **8-K item codes** (1.01 M&A, 2.02 earnings, 4.01 auditor, 5.02 exec, 7.01 Reg FD, 8.01 other) — 본문 fetch 필요
- ⬜ 10-K Item 1A Risk Factor sentiment/length
- ⬜ 10-K Item 7 (MD&A) length/sentiment
- ⬜ 10-Q forward-looking statements count
- ⬜ 10-K word complexity (Flesch reading ease, FOG index)
- ⬜ Auditor change events
- ⬜ Going concern qualifications
- ⬜ Restatement events
- ⬜ Form 4 patterns: 3-day cluster of buys
- ⬜ **13F holdings changes** (institutional flow, 분기마다 free)
- ⬜ **13D/13G** (5%+ activist holder)
- ⬜ Schedule 14A (proxy)
- ⬜ Form 144 (planned insider sales)
- ⬜ Form S-3/S-4/S-8 (registration)
- ⬜ DART 주요사항보고 (M&A, 합병/분할)
- ⬜ DART 임원·주요주주 변경 (KR insider)
- ⬜ DART 단일판매·공급계약 (대형 수주)
- ⬜ DART 합병/분할
- ⬜ **KRX 공매도 잔고** (KR-specific)
- ⬜ **KRX 외국인 보유 한도 비율**
- ⬜ KIND 공시 (한국거래소)
- ⬜ 한국 신용평가 (NICE, KIS)

### A.5 Macro (현재 18, 가능 50+)

**FRED 추가**:
- ✅ 10Y, 2Y, 3M, FEDFUNDS, CPI, M2, UE, IP, Payroll, Retail
- ⬜ **Initial Jobless Claims** (weekly leading)
- ⬜ Continued Claims
- ⬜ **ISM Manufacturing PMI**
- ⬜ **ISM Services PMI**
- ⬜ ISM New Orders
- ⬜ Manufacturers' New Orders
- ⬜ Capacity Utilization
- ⬜ Housing Starts, Building Permits
- ⬜ Existing Home Sales
- ⬜ Case-Shiller Home Price Index
- ⬜ **UMich Consumer Sentiment**
- ⬜ Conference Board Consumer Confidence
- ⬜ **PPI (Producer Price Index)**
- ⬜ **Core PCE (Fed 선호)**
- ⬜ Personal Income, PCE, Real Disposable Personal Income
- ⬜ Trade Balance, Current Account
- ⬜ Federal Debt
- ⬜ Treasury yields: 1M, 6M, 1Y, 5Y, 7Y, 20Y, 30Y
- ⬜ **TIPS yields** (real rates)
- ⬜ **Breakeven inflation** (TIPS spread)
- ⬜ Term premium
- ⬜ Effective Fed Funds Rate
- ⬜ Bank Prime Rate, SOFR

**BOK ECOS 추가**:
- ✅ 기준금리, CPI
- ⬜ KR M2 (item code 수정 필요)
- ⬜ **KR PMI** (HSBC)
- ⬜ KR Consumer Sentiment
- ⬜ KR Industrial Production
- ⬜ **KR Exports** (수출 잠정치 — leading)
- ⬜ KR Imports
- ⬜ KR Trade Balance
- ⬜ KR FX Reserves
- ⬜ KR CD Rate
- ⬜ KR Gov Bond Yields (1Y, 3Y, 5Y, 10Y)
- ⬜ KR PPI
- ⬜ KR Housing Price Index
- ⬜ KR Construction Index

**Commodities** (FRED + FDR):
- ⬜ WTI Crude, Brent Crude
- ⬜ Natural Gas
- ⬜ Silver, Copper, Steel, Coal
- ⬜ Wheat, Corn, Soybeans
- ⬜ Coffee, Sugar
- ⬜ Baltic Dry Index (shipping)

### A.6 Cross-asset (현재 18, 가능 50+)

**FX**:
- ✅ DXY
- ⬜ USD/KRW (핵심 — KR 수출주 영향)
- ⬜ USD/JPY (carry trade)
- ⬜ USD/CNY (KR 무역 영향)
- ⬜ EUR/USD
- ⬜ USD/CHF (safe haven)
- ⬜ USD/AUD (commodity FX)
- ⬜ USD EM basket

**Bond/Credit**:
- ✅ TLT
- ⬜ HYG (high yield)
- ⬜ LQD (investment grade)
- ⬜ HYG/LQD ratio (credit spread)
- ⬜ IEF (7-10Y), SHY (1-3Y), BIL (1-3M)

**Volatility**:
- ✅ VIX
- ⬜ **VVIX** (vol of vol)
- ⬜ VIX9D, VIX3M, VIX6M (term structure)
- ⬜ **MOVE** (bond vol)
- ⬜ OVX (oil vol)
- ⬜ GVZ (gold vol)
- ⬜ **CBOE SKEW**
- ⬜ VXFXI (China vol)
- ⬜ Put/Call Ratio
- ⬜ Realized vs Implied vol spread

**Commodity ETFs**:
- ✅ GLD, USO, TLT
- ⬜ UNG (nat gas), DBC (broad commodities), DBA (agriculture)
- ⬜ SLV (silver), CPER (copper)
- ⬜ Gold/Silver ratio, Copper/Gold ratio

**International**:
- ⬜ EEM, VWO (EM)
- ⬜ FXI (China)
- ⬜ EWJ (Japan)
- ⬜ EFA (developed ex-US)
- ⬜ INDA (India), EWZ (Brazil)

**Sector specifics**:
- ✅ XLK/XLF/XLV/XLE/XLY/XLP/XLI/XLB/XLU/XLRE/XLC + SPY/QQQ/IWM
- ⬜ SOXX (semiconductors)
- ⬜ IBB (biotech)
- ⬜ KRE (regional banks)
- ⬜ XHB (homebuilders)
- ⬜ JETS (airlines)
- ⬜ GDX (gold miners)

**Crypto** (sentiment proxy):
- ⬜ BTC price
- ⬜ ETH price

**Sector rotation features**:
- ⬜ XLU/XLI (defensive vs cyclical)
- ⬜ TLT/SPY (bond vs equity)

### A.7 Alternative Data (현재 0, 가능 15+)

**Free / no key**:
- ⬜ **Wikipedia pageviews API** (retail attention proxy)
- ⬜ **Google Trends** via pytrends (search interest)
- ⬜ **Reddit** r/wallstreetbets, r/stocks (PRAW free)
- ⬜ **GitHub** commit activity (tech 종목 R&D proxy)
- ⬜ **USPTO Patent filings** (R&D output)
- ⬜ Marine Traffic AIS (shipping, free tier)
- ⬜ Job postings (LinkedIn/Indeed scraping)
- ⬜ App store rankings (consumer apps)

**With existing keys**:
- ⬜ Naver DataLab 검색어 트렌드 (API 키 있음)

### A.8 Microstructure (Daily Proxies)

- ⬜ Daily range / ATR ratio
- ⬜ Opening gap %
- ⬜ Closing range % (close vs high-low range)
- ⬜ Number of distinct closes in 21d (price clustering)
- ⬜ Volume-weighted price changes
- ⬜ Effective spread proxy (CRSP-style estimator)
- ⬜ Bid-ask spread time series (if available)
- ⬜ Order book imbalance (if intraday data)
- ⬜ VPIN (Volume-synchronized Probability of Informed Trading)

### A.9 Options-derived (CBOE 일부 무료)

- ⬜ Implied volatility ATM/OTM (need source)
- ⬜ Implied skew (put 비대칭)
- ⬜ Put/Call open interest ratio
- ⬜ Options volume / stock volume
- ⬜ IV rank (현재 IV 백분위)
- ⬜ IV term structure
- ⬜ Gamma exposure (dealer positioning)

### A.10 Calendar (현재 9, 가능 25+)

- ✅ dow, dom, doq, doy, month, quarter, days_to_q_end, is_jan, is_dec
- ⬜ **FOMC meeting day flag**
- ⬜ **CPI release day flag**
- ⬜ **NFP (jobs) report day flag**
- ⬜ **PCE release day flag**
- ⬜ **GDP release day flag**
- ⬜ **FOMC minutes day flag**
- ⬜ Earnings announcement day (per-ticker)
- ⬜ Days since/to next earnings
- ⬜ Triple witching (3rd Fri Mar/Jun/Sep/Dec)
- ⬜ Options expiration (3rd Fri monthly)
- ⬜ Russell rebalance window
- ⬜ Tax loss harvest (Nov-Dec)
- ⬜ Tax day (Apr 15)
- ⬜ Halloween / Santa rally
- ⬜ Chinese New Year window
- ⬜ Korean Chuseok / Seollal
- ⬜ US election year
- ⬜ Korea presidential election

### A.11 Regime (현재 3, 가능 20+)

- ✅ regime_risk_on/off/conf (5-voter classifier)
- ⬜ **HMM (Hidden Markov Model)** 잠재 regime
- ⬜ 5/7/9-state regime
- ⬜ Days since regime change
- ⬜ Regime transition velocity
- ⬜ Sector-specific regimes
- ⬜ Cross-market regime divergence (KR vs US)
- ⬜ Vol regime (low/normal/high)
- ⬜ Trend regime (up/sideways/down)
- ⬜ Liquidity regime (tight/normal/wide spread)
- ⬜ Macro regime (expansion/peak/contraction/trough)
- ⬜ Risk-on/off intensity (continuous, not categorical)

### A.12 Network Effects

- ⬜ Mention graph centrality
- ⬜ Lead-lag with sector ETF
- ⬜ Lead-lag with most-correlated peer
- ⬜ Sector momentum spillover
- ⬜ Supply chain network position
- ⬜ Customer concentration (if known)

### A.13 Market Structure

- ⬜ Short interest ratio
- ⬜ Days to cover
- ⬜ Float / total shares
- ⬜ Insider ownership %
- ⬜ Institutional ownership %

### A.14 Text/NLP Heavy

- ⬜ Earnings call transcripts (Seeking Alpha, when available)
- ⬜ 10-K language complexity (Flesch reading ease, FOG)
- ⬜ 10-K Risk Factor section sentiment
- ⬜ 10-K MD&A section
- ⬜ Forward-looking statement counts
- ⬜ Litigation mention counts
- ⬜ Restatement detection
- ⬜ Auditor going-concern language
- ⬜ CEO letter sentiment
- ⬜ Press release tone shift

---

## B. 모델 / 방법론

### B.1 Tree-based (현재 LGBM/XGB/CatBoost)

- ✅ LightGBM (default + Optuna)
- ✅ XGBoost
- ✅ CatBoost
- ⬜ HistGradientBoosting (sklearn)
- ⬜ ExtraTreesRegressor
- ⬜ Random Forest
- ⬜ AdaBoost
- ⬜ ExplainableBoostingMachine (EBM)
- ⬜ **NGBoost** (probabilistic predictions)
- ⬜ **TabNet** (deep tabular)
- ⬜ SAINT (self-attention tabular)
- ⬜ FT-Transformer (tabular transformer)
- ⬜ TabPFN (foundation model for tabular)

### B.2 Linear / Bayesian

- ✅ Ridge (구현됨, 버그)
- ⬜ Lasso, ElasticNet, LARS
- ⬜ OMP (Orthogonal Matching Pursuit)
- ⬜ Bayesian Ridge
- ⬜ ARD (Automatic Relevance Determination)
- ⬜ Spike-and-Slab
- ⬜ Quantile Regression
- ⬜ Huber Regression (robust)
- ⬜ RANSAC
- ⬜ Theil-Sen (robust)

### B.3 Sequence / Neural

- ✅ LSTM (default)
- ⬜ LSTM Optuna tuning
- ⬜ GRU
- ⬜ BiLSTM
- ⬜ Transformer encoder
- ⬜ **TFT (Temporal Fusion Transformer)** — Google
- ⬜ **N-BEATS / N-HiTS** (시계열 SOTA)
- ⬜ **PatchTST** (Patch-based transformer 2023)
- ⬜ **iTransformer** (2024)
- ⬜ TimesNet, Crossformer
- ⬜ DLinear / NLinear (simple but strong)
- ⬜ **Chronos** (Amazon foundation model 2024)
- ⬜ **TimesFM** (Google foundation 2024)
- ⬜ **Lag-Llama** (Lag-based foundation)
- ⬜ TabTransformer
- ⬜ WaveNet (dilated conv)
- ⬜ TCN (Temporal Convolutional Network)

### B.4 Bayesian / Probabilistic

- ⬜ Gaussian Processes (소규모 + 불확실성)
- ⬜ Bayesian Neural Networks (variational)
- ⬜ Deep Kernel Learning
- ⬜ Bayesian Model Averaging
- ⬜ MC Dropout (cheap BNN approx)

### B.5 Graph

- ⬜ GCN (Graph Convolutional)
- ⬜ GAT (Graph Attention)
- ⬜ GraphSAGE
- ⬜ Knowledge Graph embeddings (TransE, RotatE)
- ⬜ Hyperbolic embeddings (계층)

### B.6 Signal Decomposition

- ⬜ Wavelet transform (Daubechies, Haar)
- ⬜ EMD (Empirical Mode Decomposition)
- ⬜ SSA (Singular Spectrum Analysis)
- ⬜ Fourier / FFT
- ⬜ TDA (Topological Data Analysis) — persistence diagrams

### B.7 Self-supervised / Pretraining

- ⬜ Contrastive learning (SimCLR-style for time series)
- ⬜ Masked Autoencoder (MAE-style)
- ⬜ TS2Vec
- ⬜ Time-series BERT pre-training

### B.8 Generative / Anomaly

- ⬜ VAE (Variational Autoencoder)
- ⬜ GAN (for synthetic augmentation)
- ⬜ Diffusion models (DDPM for time series 2024)
- ⬜ Flow-based models
- ⬜ Isolation Forest (anomaly)
- ⬜ Local Outlier Factor (LOF)
- ⬜ One-Class SVM
- ⬜ Autoencoder reconstruction error

### B.9 Meta-learning / AutoML

- ⬜ MAML (Model Agnostic Meta-Learning)
- ⬜ AutoGluon (auto ensemble)
- ⬜ FLAML (Microsoft auto ML)
- ⬜ TPOT (Genetic programming)
- ⬜ Auto-sklearn
- ⬜ Neural Architecture Search (NAS)

### B.10 Causal Inference

- ⬜ **Causal Forests** (econML, Athey 2019)
- ⬜ DoubleML
- ⬜ TMLE (Targeted Maximum Likelihood)
- ⬜ DiD (Difference-in-Differences)
- ⬜ IV (Instrumental Variables)
- ⬜ Synthetic Control

### B.11 Reinforcement Learning

- ⬜ DQN (discrete actions)
- ⬜ PPO (proximal policy)
- ⬜ A2C
- ⬜ SAC (continuous)
- ⬜ Decision Transformer (offline RL)
- ⬜ Multi-armed bandit (Thompson sampling)
- ⬜ Contextual bandits

### B.12 Finance-specific Foundation Models

- ⬜ **FinBERT** (free, 뉴스 임베딩)
- ⬜ **FinGPT** (open source, BloombergGPT 대안)
- ⬜ Sentence Transformers — all-MiniLM-L6-v2, all-mpnet-base-v2, multi-qa-mpnet-base, paraphrase-multilingual-mpnet (KR/EN 다국어)

### B.13 Stacking / Meta-ensemble

- ⬜ Stacking meta-learner (LinearRegression on OOF preds)
- ⬜ Blending (holdout-based)
- ⬜ Voting (rank-based)
- ⬜ Bayesian Model Averaging

---

## C. 검증 (현재 매우 부족)

- ✅ TimeSeriesSplit (date-grouped, embargo 21d)
- ⬜ **Combinatorial Purged CV** (López de Prado, 정통)
- ⬜ Walk-forward expanding window
- ⬜ Walk-forward rolling window
- ⬜ Triple-barrier method (López de Prado)
- ⬜ Meta-labeling
- ⬜ Cross-validation with embargo (multi-window)
- ⬜ **Deflated Sharpe Ratio** (multiple test correction)
- ⬜ **Probability of Backtest Overfitting (PBO)**
- ⬜ White's Reality Check
- ⬜ SPA test (Superior Predictive Ability)
- ⬜ Stepdown / Romano-Wolf
- ⬜ BIC / AIC for model selection
- ⬜ **Bootstrap confidence intervals** (IC, Sharpe)
- ⬜ Block bootstrap (autocorrelation 반영)
- ⬜ Stationary bootstrap
- ⬜ Information leakage detection

---

## D. 포트폴리오 구성

- ✅ Equal weight (current paper broker)
- ⬜ Mean-Variance Optimization (Markowitz)
- ⬜ **Black-Litterman**
- ⬜ **Hierarchical Risk Parity (HRP)** — López de Prado
- ⬜ Risk Parity
- ⬜ Maximum Diversification
- ⬜ Equal Risk Contribution
- ⬜ Most-Diversified Portfolio
- ⬜ Minimum Correlation
- ⬜ Inverse Volatility weighting
- ⬜ **CVaR-optimal**
- ⬜ Robust optimization (Tütüncü)
- ⬜ Resampled efficient frontier
- ⬜ Mean-CVaR
- ⬜ Mean-Drawdown optimization
- ⬜ Tail-risk parity
- ⬜ Volatility targeting overlay
- ⬜ Beta-neutral construction
- ⬜ Factor-neutral construction

---

## E. 실행 / Execution

- ✅ Market order via PaperBroker
- ⬜ Limit order
- ⬜ TWAP / VWAP execution
- ⬜ **Implementation Shortfall** (Almgren-Chriss)
- ⬜ Volume Participation Rate
- ⬜ Iceberg orders
- ⬜ Smart Order Routing
- ⬜ Adaptive slippage estimation
- ⬜ Bid-ask spread cost model
- ⬜ Market impact model (linear / sqrt)
- ⬜ Latency model
- ⬜ Order book pressure
- ⬜ Time-of-day execution profiles

---

## F. 리스크 관리

- ✅ Position size limit, daily loss, consecutive loss, max positions, allowlist
- ⬜ **Sector exposure cap**
- ⬜ Industry concentration limit
- ⬜ Beta exposure cap
- ⬜ **Vol-adjusted position sizing**
- ⬜ **Kelly Criterion** (full / fractional)
- ⬜ VaR limit
- ⬜ CVaR limit
- ⬜ Drawdown circuit breaker
- ⬜ Stress test triggers
- ⬜ Correlation risk (cluster-based)
- ⬜ Liquidity-adjusted sizing
- ⬜ Slippage budget
- ⬜ Tail hedging (long puts)
- ⬜ Pair trading risk overlay
- ⬜ Conditional drawdown control

---

## G. 거래비용 모델

- ⬜ KR 거래세 0.18% (KOSPI), 0.18% (KOSDAQ)
- ⬜ KR 거래수수료 (broker별 다름, 평균 0.015%)
- ⬜ 미국 SEC fee + FINRA fee
- ⬜ 미국 commission (보통 0, Alpaca 0)
- ⬜ Slippage model (linear / sqrt by volume)
- ⬜ Spread cost (bid-ask)
- ⬜ Borrow cost for shorts
- ⬜ FX conversion cost (KR <-> US)
- ⬜ Margin interest
- ⬜ Tax (KR 양도소득세 — 대주주만)
- ⬜ Wash sale rules (US)

---

## H. MLOps 인프라

- ✅ Model registry (var/models/runs/...)
- ✅ Ensemble spec (var/models/ensembles.json)
- ✅ Drift detector (KS-test + rolling IC)
- ✅ Scheduler integration (mlops.retrain.weekly, drift_check.daily)
- ⬜ Feature Store (Feast or custom)
- ⬜ Online inference service (FastAPI + Redis cache)
- ⬜ Prometheus metrics export
- ⬜ Grafana dashboards
- ⬜ Real-time data streaming (Kafka / Redis Streams)
- ⬜ Event sourcing (audit log)
- ⬜ A/B testing framework
- ⬜ Shadow mode (paper alongside live)
- ⬜ Champion-Challenger
- ⬜ Multi-armed bandit for live selection
- ⬜ Canary deployment
- ⬜ Rollback automation
- ⬜ Health checks
- ⬜ SLI/SLO monitoring
- ⬜ Alerting (Slack, email)
- ⬜ Model card documentation
- ⬜ Datasheets for datasets
- ⬜ Lineage tracking
- ⬜ Experiment tracking (**MLflow**)
- ⬜ Hyperparameter tracking
- ⬜ Confusion matrix / calibration plots
- ⬜ **SHAP** explanations
- ⬜ LIME explanations
- ⬜ **Permutation importance**
- ⬜ Partial dependence plots
- ⬜ Counterfactual explanations
- ⬜ Feature importance stability tracking
- ⬜ **Prediction interval / uncertainty quantification**
- ⬜ **Conformal prediction**

---

## I. 모니터링 / 운영

- ⬜ Real-time P&L
- ⬜ Daily P&L attribution
- ⬜ Sector P&L attribution
- ⬜ Model P&L attribution
- ⬜ Factor attribution (Fama-French 5)
- ⬜ Risk dashboard
- ⬜ Position dashboard
- ⬜ Trade log
- ⬜ Order rejection log
- ⬜ Slippage tracking
- ⬜ Spread tracking
- ⬜ Latency tracking
- ⬜ Feature drift heatmap
- ⬜ Concept drift detection
- ⬜ Performance decay tracking
- ⬜ Anomaly alerts
- ⬜ Risk limit breaches
- ⬜ Model performance reports (daily/weekly/monthly)
- ⬜ Tax accounting
- ⬜ Compliance reports
- ⬜ Operations runbook
- ⬜ Disaster recovery procedures

---

## J. Walk-forward Backtesting / Simulation

- ✅ Historical replay (basic, rescoring_runner.py)
- ⬜ Walk-forward backtest engine (proper)
- ⬜ Monte Carlo path simulation
- ⬜ Bootstrap backtest
- ⬜ Block bootstrap
- ⬜ Stationary bootstrap
- ⬜ Stress scenarios (2008, 2020, 2022 COVID, China property)
- ⬜ Worst-case scenarios
- ⬜ Latency simulation
- ⬜ Slippage simulation
- ⬜ Liquidity-limited execution simulation
- ⬜ Multi-asset portfolio simulation
- ⬜ Realistic costs (fees, taxes, borrow rate for shorts)
- ⬜ Cash account simulation
- ⬜ Margin simulation
- ⬜ Multi-currency support
- ⬜ Time zone handling
- ⬜ Holiday calendar awareness (KR + US)
- ⬜ Half-day trading
- ⬜ Multiple brokers simulation
- ⬜ Position reconciliation
- ⬜ Reporting (PnL, Sharpe, drawdown, MDD recovery)

---

## K. 클러스터 커버리지 (현재 ~10 / 22)

**A등급 production 후보** (이미 등록):
- ✅ US:ENERGY:LARGE (tuned LGBM IC +0.353)
- ✅ US:FIN:LARGE (default CatBoost +0.230)
- ✅ KR:FIN:LARGE (default CatBoost +0.262)
- ✅ US:UTILITIES:LARGE (등록 중)

**B등급 검토 중**:
- ✅ KR:OTHER:SMALL (default CatBoost +0.209)
- ✅ KR:OTHER:LARGE (tuned CatBoost +0.091, hit 69%)
- ✅ KR:FIN:MID (default CatBoost +0.103)
- ⬜ KR:FIN:SMALL (tuning 진행 중)

**아직 미처리**:
- ⬜ US:TECH:LARGE (default 저장 중)
- ⬜ US:INDUSTRIALS:LARGE (default 저장 중)
- ⬜ US:HEALTH:LARGE
- ⬜ US:CONS_DISC:LARGE
- ⬜ US:CONS_STAPLES:LARGE (음수 IC — ignore 후보)
- ⬜ US:MATERIALS:LARGE
- ⬜ US:REAL_ESTATE:LARGE
- ⬜ US:COMM:LARGE
- ⬜ US:REAL_ESTATE:MID
- ⬜ US:COMM:MID
- ⬜ US:FIN:MID
- ⬜ US:INDUSTRIALS:MID
- ⬜ US:CONS_STAPLES:MID

---

## L. 다중 horizon target (현재 21d 단일)

- ✅ ret_fwd_21d, rank_fwd_21d (현재 학습 중)
- ⬜ ret_fwd_5d 학습 + 등록
- ⬜ ret_fwd_63d 학습 + 등록
- ⬜ vol_adj_ret_21d (위험조정)
- ⬜ Multi-horizon signal combiner (방향 합치 가중)

---

## M. KR/US 통합 시도

- ⬜ Cross-market lead-lag (US 어제 → KR 오늘)
- ⬜ Cointegration pairs (삼성전자 vs 마이크론)
- ⬜ Currency-adjusted relative momentum
- ⬜ Cross-listed ADR analysis (KR ADR)

---

## N. 외부 데이터 — 미발급 키 / 차단

- ⛔ BIGKinds (유료 전환)
- ⛔ Reddit API (가입 실패)
- ⬜ Bloomberg Terminal (유료, 보류)
- ⬜ Refinitiv (유료)
- ⬜ FactSet (유료)
- ⬜ Seeking Alpha PRO (유료 transcript)
- ⬜ Trefis (유료 estimates)

---

## 우선순위 / 처리 순서

**Wave 1 — 즉시 시작 (무료, 기존 데이터 활용)**:
1. GDELT V2Tone/V2Themes 직접 추출 (이미 DB 있음)
2. Wikipedia pageviews, Google Trends, Naver DataLab
3. Piotroski / Altman / Beneish 구현
4. 8-K item code 본문 fetch + 파싱
5. FRED 추가 시리즈 일괄 추가
6. Microstructure daily proxies
7. Calendar 이벤트 flags
8. HMM regime detector
9. Multi-horizon labels (5d/21d/63d)
10. pandas-ta 100+ 미사용 지표
11. TA-Lib candlestick 60+ patterns
12. Fundamental sector-relative percentile
13. Statistical features (rolling beta, etc.)

**Wave 2 — 신모델 + 검증 인프라**:
14. CPCV (Combinatorial Purged CV) 구현
15. Deflated Sharpe Ratio, PBO 구현
16. Bootstrap CI 구현
17. Walk-forward proper engine
18. 거래비용 모델
19. HRP, Black-Litterman 포트폴리오
20. Kelly + vol targeting sizer
21. TFT, N-BEATS, PatchTST 학습
22. Chronos, TimesFM foundation 활용
23. Causal Forest
24. GP, BNN 베이지안
25. Self-supervised pretrain
26. FinBERT 뉴스 임베딩
27. Stacking meta-learner

**Wave 3 — Production + 운영**:
28. Feature Store
29. Online inference service (FastAPI)
30. SHAP/LIME 설명
31. Conformal prediction (예측 구간)
32. MLflow experiment tracking
33. Grafana dashboard
34. Shadow trading
35. Champion-challenger
36. Real-time streaming
37. Stress testing
38. Compliance / tax accounting

**Wave 4 — 알파 발견 자동화**:
39. AutoML / Genetic Programming
40. Meta-learning
41. Anomaly detection (VAE)
42. Reinforcement learning execution
43. GNN sector network
44. Multi-asset / cross-market signals

---

## O. 전략 / 알파 패밀리 (현재 단일 long-only forward return)

- ⬜ **Long-short strategy** (롱 top quintile / 숏 bottom quintile)
- ⬜ **Market-neutral** (베타 헤지)
- ⬜ **Sector-neutral** (섹터 노출 0)
- ⬜ **Factor-neutral** (Fama-French 5 헤지)
- ⬜ **Pair trading / Statistical Arbitrage**
- ⬜ **PEAD (Post-Earnings Announcement Drift)** 전용 전략
- ⬜ **Sentiment arbitrage** (뉴스 sentiment vs 실제 반응)
- ⬜ **Volatility timing** (별도 vol 예측 → 포지션 사이즈 조정)
- ⬜ **Volatility risk premium** harvesting
- ⬜ **Dividend yield strategy** (고배당 + quality)
- ⬜ **Low volatility anomaly** (BAB - Betting Against Beta)
- ⬜ **Quality minus junk (QMJ)**
- ⬜ **Profitability factor** (Novy-Marx)
- ⬜ **Investment factor** (CMA)
- ⬜ **Earnings revision strategy** (analyst 예상치 변경)
- ⬜ **Index inclusion/deletion** (Russell, S&P add/drop)
- ⬜ **Buyback announcement** post-event drift
- ⬜ **Spin-off** outperformance
- ⬜ **IPO 1-3 yr** underperformance
- ⬜ **Insider trading follow** (Form 4 cluster buy → 60d hold)
- ⬜ **13F follow** (smart money tracking)
- ⬜ **Activist investor follow** (13D 후 N일)
- ⬜ **Momentum crash** detection / avoidance
- ⬜ **Mean reversion** vs momentum regime switching

---

## P. 라벨 / Target Engineering (현재 단순 forward return)

- ✅ ret_fwd_5d/21d/63d (계산만)
- ✅ rank_fwd_5d/21d (cross-sectional rank)
- ✅ vol_adj_ret_21d
- ⬜ **Triple-barrier method** (López de Prado) — 익절/손절/시간만료
- ⬜ **Meta-labeling** (primary model → secondary 신뢰도 모델)
- ⬜ **Sample weighting** (시간 가중 / 변동성 가중)
- ⬜ **Path-dependent labels** (최대 DD 동안 손실 났는지)
- ⬜ **Multi-target** simultaneous (return + vol + drawdown)
- ⬜ **Quantile binning** (-2σ ~ +2σ를 quintile로)
- ⬜ **Sign-only** target (방향만)
- ⬜ **Probability of outperformance** (cross-sectional binary)

---

## Q. 피처 엔지니어링 기법 (현재 raw만)

- ⬜ **Polynomial features** (x², x³, interactions)
- ⬜ **Feature interactions** 명시 (ratios, differences, products)
- ⬜ **Target encoding** for categorical (cluster_id, market, sector_bucket, regime_label)
- ⬜ **Feature scaling** for neural models (StandardScaler, RobustScaler)
- ⬜ **PCA** for dimensionality reduction
- ⬜ **Kernel PCA** for nonlinear
- ⬜ **t-SNE / UMAP** for visualization (offline)
- ⬜ **Mutual information** feature selection
- ⬜ **Boruta** feature selection
- ⬜ **Recursive Feature Elimination (RFE)**
- ⬜ **Lasso path** for stable feature selection
- ⬜ **Feature importance stability** (cross-fold consistency)
- ⬜ **Synthetic Minority Oversampling (SMOTE)** — class imbalance 라벨 시

---

## R. 데이터 품질 / Bias

- ⬜ **Survivorship bias correction** — 상폐 종목 historical 포함
- ⬜ **Look-ahead bias 자동 검출** (point-in-time DB 검증)
- ⬜ **Data quality checks** (schema, range, null rate)
- ⬜ **Outlier detection per feature**
- ⬜ **Corporate actions handling** (splits adjustment 확인)
- ⬜ **Merger/acquisition handling**
- ⬜ **Delisting handling** (return = -100% in last day)
- ⬜ **Stock splits** (currently adj_close에 반영, 별도 검증 필요)
- ⬜ **Dividends** (return total return 인지 price return 인지)
- ⬜ **Currency conversion** (USD/KRW 시계열 일관성)
- ⬜ **Time zone consistency** (KR vs US 시간)
- ⬜ **Holiday calendar** (양 시장 휴장일)
- ⬜ **Half-day trading** — US: Black Friday(11월 4번째 금요일 후), Christmas Eve, July 3 (조기 1pm 마감). KR: 매년 거래소 발표 (보통 폐장일 13:30 마감)

---

## S. 실거래 인프라

- ✅ Paper broker
- ⬜ **KIS Developers API** 어댑터 (KR live)
- ⬜ **Alpaca API** 어댑터 (US live)
- ⬜ Pre-trade compliance check
- ⬜ Post-trade reconciliation
- ⬜ Fill quality analysis
- ⬜ Live position vs DB reconciliation
- ⬜ Settlement T+2 simulation
- ⬜ Margin call simulation
- ⬜ Pattern Day Trader rule (US <$25k)
- ⬜ Wash sale prevention (US tax)
- ⬜ Tax lot tracking (FIFO/LIFO/specific)
- ⬜ Borrow availability check (shorts)
- ⬜ Locate cost tracking
- ⬜ Real-time price feed
- ⬜ Real-time news feed
- ⬜ Real-time disclosure feed
- ⬜ Latency monitoring (broker round-trip)

---

## T. 문서 / 운영

- ⬜ API documentation (Swagger/OpenAPI)
- ⬜ Model cards (Google standard)
- ⬜ Datasheets for datasets
- ⬜ Decision documents (ADR — Architecture Decision Records)
- ⬜ Failure mode documentation
- ⬜ Disaster recovery procedures
- ⬜ Backup / restore procedures
- ⬜ Security audit
- ⬜ Encryption at rest / in transit
- ⬜ Access control (RBAC)
- ⬜ Audit log (who deployed what when)
- ⬜ CI/CD pipeline (GitHub Actions)
- ⬜ Pre-commit hooks (lint, type-check, test)
- ⬜ Code coverage tracking

---

## U. 학술 SOTA 참고 (추가 reading list)

- ⬜ Cochrane "Discount Rates" — long-term return predictability
- ⬜ Hou-Xue-Zhang 4-factor model
- ⬜ Lewellen "Cross-Sectional Returns" — predictability
- ⬜ Israel-Moskowitz "The Role of Shorting"
- ⬜ Asness "Quality Minus Junk"
- ⬜ Frazzini-Pedersen "Betting Against Beta"
- ⬜ Daniel-Moskowitz "Momentum Crashes"
- ⬜ DeMiguel-Garlappi-Uppal "1/N portfolio"
- ⬜ López de Prado "Advances in Financial ML" (전체 다 적용)
- ⬜ Gu-Kelly-Xiu "Empirical Asset Pricing via ML" (벤치마크)

---

## V-pre. 추가 발견 (3차 자가검토)

### 시계열 통계 / Econometrics
- ⬜ **Fractional Differencing** (López de Prado) — memory 유지하며 stationary
- ⬜ **ADF/KPSS stationarity test**
- ⬜ **Cointegration tests** (Engle-Granger, Johansen)
- ⬜ **Granger causality** test (US ETF → KR 종목)
- ⬜ **Vector Autoregression (VAR)** 다변량
- ⬜ **VECM** (Vector Error Correction Model)
- ⬜ **Kalman filter** (state space, dynamic linear models)
- ⬜ **Particle filter**
- ⬜ **Bayesian Structural Time Series** (Google CausalImpact)
- ⬜ **DCC-GARCH** (dynamic conditional correlation)

### 행동재무 / Behavioral Finance
- ⬜ **Prospect theory** weighting (loss aversion 2.5x)
- ⬜ **Mental accounting** category-specific risk
- ⬜ **Disposition effect** indicators (보유 기간 + 수익률)
- ⬜ **Overreaction / Underreaction** windows
- ⬜ **Anchoring** (52-week high 거리)
- ⬜ **Herding** indicators (volume + correlation spikes)

### 정보이론
- ⬜ **Shannon entropy** of returns
- ⬜ **Mutual information** between features
- ⬜ **Conditional entropy**
- ⬜ **Transfer entropy** (방향성 있는 information flow)

### 손실함수
- ⬜ **Asymmetric loss** (big loss > big gain importance)
- ⬜ **Quantile loss** (pinball)
- ⬜ **Sharpe-aware loss** (직접 Sharpe 최대화)
- ⬜ **IC-aware loss** (직접 IC 최대화)
- ⬜ **Tilted absolute loss**
- ⬜ **Huber loss** (outlier robust)

### Backtesting 라이브러리 활용
- ⬜ **vectorbt** (이미 설치, 본격 미사용)
- ⬜ **backtrader**
- ⬜ **zipline-reloaded**
- ⬜ **bt** (flexible portfolio backtesting)
- ⬜ **pyfolio** (성과 분석)
- ⬜ **quantstats** (Sharpe/Sortino/Calmar 자동 리포트, 설치됨)
- ⬜ **alphalens** (factor analysis)
- ⬜ **empyrical** (성과 metric)

### 주문 관리
- ⬜ **Order Management System (OMS)** 구조
- ⬜ **Position blotter** (실시간 포지션)
- ⬜ **Trade ticket** generation
- ⬜ **Order book reconstruction**
- ⬜ **FIX protocol** (향후 institutional 거래 시. 현재 개인 운영엔 불필요)

### Monitoring 추가
- ⬜ **Cohort analysis** (when trained vs current performance)
- ⬜ **Survival analysis** (model lifecycle, 언제 deprecate)
- ⬜ **Champion-Challenger 자동 승강**
- ⬜ **Statistical Process Control** (SPC) charts

### 한국 추가
- ⬜ **DART 정정공시** detection
- ⬜ **DART 자본금 변경**
- ⬜ **회계처리방법 변경**
- ⬜ **대주주 변경**
- ⬜ **자기주식 취득/처분**
- ⬜ **KOSDAQ vs KOSPI 시장이전**
- ⬜ **관리종목/투자위험 지정**

### 미국 추가
- ⬜ **SEC 13F-HR/13F-HR/A** parsing
- ⬜ **Form 144** (planned sales)
- ⬜ **Schedule 13D/G** (5% holders)
- ⬜ **Form 5** (annual insider summary)
- ⬜ **Beneficial Ownership Reports**
- ⬜ **DEF 14A** (proxy statement compensation)
- ⬜ **Russell rebalance** (June reconstitution)
- ⬜ **S&P committee** index changes
- ⬜ **MSCI** ESG ratings (free tier?)

### 신경망 추가 기법
- ⬜ **Curriculum learning** (easy → hard)
- ⬜ **Domain adaptation** (KR ↔ US transfer)
- ⬜ **Multi-task learning** (return + vol 동시)
- ⬜ **Attention mechanism** (시간 단계별 가중)
- ⬜ **Positional encoding** for time series
- ⬜ **Mixture of Experts (MoE)** per cluster routing

### 강건성 / 견고성
- ⬜ **Adversarial training** (feature 교란 robust)
- ⬜ **Robust regression** (Huber, M-estimator)
- ⬜ **Differential privacy** (개인정보 보호 — 향후 클라이언트 운영 시)
- ⬜ **Model card with fairness metrics**

### 학습 다양화
- ⬜ **Online learning** (incremental update)
- ⬜ **Federated learning** (multi-source)
- ⬜ **Active learning** (uncertainty query)
- ⬜ **Few-shot learning** (new ticker)

---

## V. 회수율 / 자기점검 항목

이 GAPS.md 자체에 대한 체크 (작업 시작 시마다):

- [ ] 마지막 검토일로부터 *새로 시도해본 것* 있나? GAPS에 결과 반영했나?
- [ ] *EXCLUDED* 표시한 거 *이유* 명시했나?
- [ ] 한 카테고리만 deep-dive하고 다른 카테고리 무시하고 있지 않나?
- [ ] WORK_LOG와 GAPS.md 둘 다 갱신했나?
- [ ] 사용자가 발견하기 전에 누락 발굴했나?
