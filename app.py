import streamlit as st
import pandas as pd
import cloudscraper
import requests
from datetime import datetime, timedelta
import plotly.graph_objects as go

# Alpha Vantage API Key is loaded from Streamlit secrets (set in your secrets.toml under [alpha_vantage])
ALPHA_VANTAGE_KEY = st.secrets.alpha_vantage.key

# Streamlit setup
st.set_page_config(page_title="Insider Trading Dashboard", layout="wide")
st.title("📈 Insider Trading Dashboard")

# (Feeds are selected via sidebar now)

# Feed definitions
FEEDS = {
    "Latest Insider Purchases":  "insider-purchases",
    "Latest Insider Sales":      "insider-sells",
    "Purchases > $25 K":         "insider-purchases?pfl=25",
    "Sales > $100 K":            "insider-sells?pfl=100",
    "CEO/CFO Purchases > $25 K": "insider-purchases?plm=25&pft=CEO,CFO",
}

# Helper functions

def normalize_cols(cols):
    return [str(c).replace("\xa0", " ").strip() for c in cols]

def find_table_with_filing(tables):
    for tbl in tables:
        cols = normalize_cols(tbl.columns)
        if "Filing Date" in cols and "Trade Date" in cols:
            tbl.columns = cols
            return tbl
    return None

def find_col(cols, *keywords):
    for c in cols:
        low = c.lower()
        for kw in keywords:
            if kw in low:
                return c
    return None

# Basic signal strength per trade

def calculate_signal_strength(row):
    score = 0
    if row['Shares'] >= 1_000_000:
        score += 35
    elif row['Shares'] >= 500_000:
        score += 25
    elif row['Shares'] >= 100_000:
        score += 15
    elif row['Shares'] >= 25_000:
        score += 5
    title = row['Title'].lower()
    if 'ceo' in title or 'chief executive' in title:
        score += 30
    elif 'cfo' in title:
        score += 20
    elif 'director' in title or 'officer' in title:
        score += 10
    if row['Price'] <= 2:
        score += 10
    elif row['Price'] <= 5:
        score += 5
    return score

# Cluster detection

def detect_clusters(df, days_window=7, min_insiders=3):
    clusters = []
    for ticker, group in df.groupby('Ticker'):
        grp = group.sort_values('TradeDate')
        for idx, row in grp.iterrows():
            window_start = row['TradeDate'] - timedelta(days=days_window)
            window_df = grp[(grp['TradeDate'] >= window_start) & (grp['TradeDate'] <= row['TradeDate'])]
            insiders = window_df['InsiderName'].nunique()
            if insiders >= min_insiders:
                total_shares = window_df['Shares'].sum()
                cluster_score = window_df['SignalStrength'].sum() + insiders * 5
                clusters.append({
                    'Ticker': ticker,
                    'EndDate': row['TradeDate'],
                    'NumInsiders': insiders,
                    'TotalShares': total_shares,
                    'ClusterScore': cluster_score,
                    'WindowStart': window_start
                })
    return pd.DataFrame(clusters)

# Fetch daily data

def fetch_price_data(symbol):
    url = 'https://www.alphavantage.co/query'
    params = {
        'function': 'TIME_SERIES_DAILY_ADJUSTED',
        'symbol': symbol,
        'outputsize': 'compact',
        'apikey': ALPHA_VANTAGE_KEY
    }
    r = requests.get(url, params=params)
    data = r.json().get('Time Series (Daily)', {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty or '5. adjusted close' not in df.columns:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df = df.rename(columns={'5. adjusted close': 'AdjClose'})
    return df[['AdjClose']].sort_index()

# Fetch intraday data

def fetch_intraday_data(symbol, interval='5min', outputsize='compact', adjusted=True, extended_hours=True):
    url = 'https://www.alphavantage.co/query'
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': symbol,
        'interval': interval,
        'apikey': ALPHA_VANTAGE_KEY,
        'outputsize': outputsize,
        'adjusted': str(adjusted).lower(),
        'extended_hours': str(extended_hours).lower(),
    }
    r = requests.get(url, params=params)
    key = f'Time Series ({interval})'
    data = r.json().get(key, {})
    df = pd.DataFrame.from_dict(data, orient='index')
    if df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index)
    df.columns = [col.split('. ')[1] for col in df.columns]
    return df.rename(columns={'close':'AdjClose'}).sort_index()

# Sidebar inputs
feeds = st.sidebar.multiselect("Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"])
min_insiders = st.sidebar.number_input("Min insiders for cluster", min_value=2, max_value=10, value=3)
days_window = st.sidebar.number_input("Cluster window days", min_value=1, max_value=30, value=7)
use_intraday = st.sidebar.checkbox("Use Intraday Data", value=False)
interval = st.sidebar.selectbox("Intraday interval", ["1min","5min","15min","30min","60min"], index=1)
refresh_clicked = st.sidebar.button("🔄 Refresh Data")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    ### How to Use This Dashboard
    - Select your desired feeds and cluster settings.
    - Toggle intraday to see OHLCV candles intraday or daily adjusted close.
    - Pick an interval when using intraday (1–60 minute bars).
    - Hit Refresh to fetch the latest insider trades and price series.
    """,
    unsafe_allow_html=True
)

# Data fetch
if refresh_clicked:
    scraper = cloudscraper.create_scraper(browser={"browser":"chrome","platform":"windows","desktop":True})
    all_dfs=[]
    for name in feeds:
        endpoint=FEEDS[name]
        feed_dfs=[]
        for offset in [0,100,200]:
            sep='&' if '?' in endpoint else '?'
            resp=scraper.get(f"http://openinsider.com/{endpoint}{sep}o={offset}")
            tables=pd.read_html(resp.text,flavor='bs4')
            df0=find_table_with_filing(tables)
            if not df0: continue
            cols=df0.columns.tolist()
            mapping={k:find_col(cols,*v) for k,v in {
                'FilingDate':['filing date'], 'TradeDate':['trade date'], 'Ticker':['ticker'],
                'InsiderName':['insider name'], 'Title':['title'], 'TradeType':['trade type'],
                'Shares':['qty','share'], 'Price':['price']
            }.items()}
            if any(v is None for v in mapping.values()): continue
            df=pd.DataFrame({
                'FilingDate':df0[mapping['FilingDate']],
                'TradeDate':pd.to_datetime(df0[mapping['TradeDate']]),
                'Ticker':df0[mapping['Ticker']],
                'InsiderName':df0[mapping['InsiderName']],
                'Title':df0[mapping['Title']],
                'TradeType':df0[mapping['TradeType']],
                'Shares':df0[mapping['Shares']].astype(str).replace(r"[+,]","",regex=True).astype(int),
                'Price':df0[mapping['Price']].astype(str).replace(r"[\$,]","",regex=True).astype(float)
            })
            df=df[df['TradeType'].str.contains('purchase',case=False,na=False)]
            df['SignalStrength']=df.apply(calculate_signal_strength,axis=1)
            feed_dfs.append(df)
        if feed_dfs: all_dfs.append(pd.concat(feed_dfs,ignore_index=True))
    if not all_dfs: st.error("No data fetched"); st.stop()
    data=pd.concat(all_dfs,ignore_index=True)
    st.session_state['data']=data
    st.success(f"✅ Fetched {len(data)} insider trades.")
else:
    data=st.session_state.get('data',pd.DataFrame())

if data.empty:
    st.info("No data — please refresh."); st.stop()

# Display tables
c1,c2=st.columns((2,1))
c1.markdown("### All Insider Buys")
c1.dataframe(data[['FilingDate','TradeDate','Ticker','InsiderName','Title','Shares','Price','SignalStrength']],use_container_width=True)
c2.markdown("### Top 5 by Signal Strength")
c2.dataframe(data.nlargest(5,'SignalStrength')[['Ticker','InsiderName','Shares','Price','SignalStrength']],use_container_width=True)

# Cluster analysis
clusters=detect_clusters(data,days_window,min_insiders)
if not clusters.empty:
    st.markdown("---")
    st.markdown("## Clustered Trading Analysis")
    st.dataframe(clusters.sort_values('ClusterScore',ascending=False),use_container_width=True)
    ticker_choice=st.selectbox("Select ticker for price chart",options=clusters['Ticker'].unique())
    if use_intraday:
        price_df=fetch_intraday_data(ticker_choice,interval)
    else:
        price_df=fetch_price_data(ticker_choice)
    if price_df.empty:
        st.warning(f"No price data for {ticker_choice}.")
    else:
        fig=go.Figure(); fig.add_trace(go.Candlestick(
            x=price_df.index,
            open=price_df['open'] if use_intraday else price_df['AdjClose'],
            high=price_df['high'] if use_intraday else price_df['AdjClose'],
            low=price_df['low'] if use_intraday else price_df['AdjClose'],
            close=price_df['AdjClose'], name='Price'
        ))
        for _,cl in clusters[clusters['Ticker']==ticker_choice].iterrows():
            point=cl['EndDate']; val=price_df['AdjClose'].get(point)
            fig.add_trace(go.Scatter(x=[point],y=[val],mode='markers',marker=dict(size=10+cl['NumInsiders']*3),name=f"Cluster {cl['NumInsiders']}"))
        st.plotly_chart(fig,use_container_width=True)

# Test notification remains
if st.button("Send Test Notification"):
    # ... unchanged ...
    pass
