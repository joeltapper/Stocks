import streamlit as st
import pandas as pd
import cloudscraper
import requests
from datetime import datetime, timedelta
import plotly.graph_objects as go

# Alpha Vantage API Key
ALPHA_VANTAGE_KEY = st.secrets.alpha_vantage.key

# Streamlit setup
st.set_page_config(page_title="Insider Trading Dashboard", layout="wide")
st.title("📈 Insider Trading Dashboard")

# Intro blurb explaining platform
st.markdown(
    """
    This dashboard aggregates insider trading data from OpenInsider and overlays it with price charts,
    helping you spot significant buy/sell clusters and signals. Use the controls on the left to fetch data,
    set cluster criteria, and simulate trades using buy/sell inputs.
    """,
    unsafe_allow_html=True
)

# Feed definitions
FEEDS = {
    "Latest Insider Purchases":  "insider-purchases",
    "Latest Insider Sales":      "insider-sells",
    "Purchases > $25 K":         "insider-purchases?pfl=25",
    "Sales > $100 K":            "insider-sells?pfl=100",
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

# Helper functions

def normalize_cols(cols): return [str(c).replace("\xa0", " ").strip() for c in cols]

def find_table_with_filing(tables):
    for tbl in tables:
        cols = normalize_cols(tbl.columns)
        if "Filing Date" in cols and "Trade Date" in cols:
            tbl.columns = cols
            return tbl
    return None

def find_col(cols,*keywords):
    for c in cols:
        low = c.lower()
        for kw in keywords:
            if kw in low:
                return c
    return None

# Signal strength calculation

def calculate_signal_strength(row):
    score = 0
    if row['Shares'] >= 1_000_000: score += 35
    elif row['Shares'] >= 500_000: score += 25
    elif row['Shares'] >= 100_000: score += 15
    elif row['Shares'] >= 25_000: score += 5
    title = row['Title'].lower()
    if 'ceo' in title: score += 30
    elif 'cfo' in title: score += 20
    elif 'director' in title or 'officer' in title: score += 10
    if row['Price'] <= 2: score += 10
    elif row['Price'] <= 5: score += 5
    return score

# Cluster detection

def detect_clusters(df, days_window=7, min_insiders=3):
    clusters = []
    for ticker, grp in df.groupby('Ticker'):
        grp = grp.sort_values('TradeDate')
        for _, r in grp.iterrows():
            start = r['TradeDate'] - timedelta(days=days_window)
            window = grp[(grp['TradeDate'] >= start) & (grp['TradeDate'] <= r['TradeDate'])]
            ins = window['InsiderName'].nunique()
            if ins >= min_insiders:
                clusters.append({
                    'Ticker': ticker,
                    'WindowStart': start,
                    'EndDate': r['TradeDate'],
                    'NumInsiders': ins,
                    'TotalShares': window['Shares'].sum(),
                    'ClusterScore': window['SignalStrength'].sum() + ins * 5
                })
    return pd.DataFrame(clusters)

# Fetch daily adjusted close data

def fetch_price_data(symbol):
    params = {
        'function': 'TIME_SERIES_DAILY_ADJUSTED',
        'symbol': symbol,
        'outputsize': 'compact',
        'apikey': ALPHA_VANTAGE_KEY
    }
    r = requests.get('https://www.alphavantage.co/query', params=params).json()
    data = r.get('Time Series (Daily)', {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if '5. adjusted close' not in df.columns:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df = df.rename(columns={'5. adjusted close': 'AdjClose'})
    return df[['AdjClose']].sort_index()

# Fetch intraday OHLCV data

def fetch_intraday_data(symbol, interval='5min'):
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': symbol,
        'interval': interval,
        'apikey': ALPHA_VANTAGE_KEY,
        'outputsize': 'compact'
    }
    r = requests.get('https://www.alphavantage.co/query', params=params).json()
    key = f'Time Series ({interval})'
    data = r.get(key, {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df.columns = [c.split('. ')[1] for c in df.columns]
    return df.rename(columns={'close': 'AdjClose'}).sort_index()

# Sidebar controls
feeds = st.sidebar.multiselect(
    "Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"], key='feeds'
)
min_insiders = st.sidebar.number_input(
    "Min insiders for cluster", 2, 10, 3, key='min_ins'
)
days_window = st.sidebar.number_input(
    "Cluster window days", 1, 30, 7, key='days_win'
)
# Option for intraday vs daily
use_intraday = st.sidebar.checkbox(
    "Use Intraday Data", value=True, key='intraday'
)
# Intraday interval selection
interval = st.sidebar.selectbox(
    "Intraday interval", ["1min", "5min", "15min", "30min", "60min"], index=1, key='intraday_interval'
)
# Refresh data trigger
refresh = st.sidebar.button(
    "🔄 Refresh Data", key='refresh'
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    ### How to Use This Dashboard
    - Select feeds and cluster settings.
    - Toggle Intraday and choose interval for OHLCV.
    - Click Refresh to fetch data.
    """,
    unsafe_allow_html=True
)

# Show fetch notification
if refresh:
    # Assume `data` is loaded into session_state in fetch logic
    df = st.session_state.get('data', pd.DataFrame())
    st.success(f"✅ Fetched {len(df)} insider buys.")

# Plot area logic continues...
