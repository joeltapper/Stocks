import streamlit as st
import pandas as pd
import cloudscraper
import requests
from io import StringIO
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

# Signal strength calculation

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
    if 'ceo' in title:
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

# Fetch intraday OHLCV data with full history, extended hours & error handling

def fetch_intraday_data(symbol, interval='5min'):
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': symbol,
        'interval': interval,
        'apikey': ALPHA_VANTAGE_KEY,
        'outputsize': 'full',          # full 30 days of bars
        'adjusted': 'true',            # split/dividend adjusted
        'extended_hours': 'true',      # include pre/post-market
        'datatype': 'csv'              # CSV for easier parsing
    }
    resp = requests.get('https://www.alphavantage.co/query', params=params)
    # first try JSON to catch rate-limit or bad-key errors
    try:
        j = resp.json()
        if isinstance(j, dict):
            if 'Note' in j:
                st.error("⏱️ Alpha Vantage rate limit hit—please wait and retry.")
            elif 'Error Message' in j:
                st.error(f"❌ Alpha Vantage error: {j['Error Message']}")
            return pd.DataFrame()
    except ValueError:
        pass

    # otherwise parse the CSV payload
    df = pd.read_csv(StringIO(resp.text), parse_dates=['timestamp'], index_col='timestamp')
    return df.sort_index()

# Sidebar controls
feeds = st.sidebar.multiselect(
    "Select OpenInsider feeds to include", list(FEEDS), default=["Latest Insider Purchases"]
)
min_insiders = st.sidebar.number_input("Min insiders for cluster", 2, 10, 3)
days_window = st.sidebar.number_input("Cluster window days", 1, 30, 7)
interval = '5min'
refresh = st.sidebar.button("🔄 Refresh Data")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    ### How to Use This Dashboard
    - Select feeds and cluster settings.
    - Chart shows 5-min intraday candlesticks by default.
    - Enter buy/sell prices below to simulate returns.
    - Click Refresh to fetch data.
    """,
    unsafe_allow_html=True
)

# Data fetch
if refresh:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    all_dfs = []
    for name in feeds:
        ep = FEEDS[name]
        dfs = []
        for off in [0, 100, 200]:
            sep = '&' if '?' in ep else '?'
            url = f"http://openinsider.com/{ep}{sep}o={off}"
            tables = pd.read_html(scraper.get(url).text, flavor='bs4')
            df0 = find_table_with_filing(tables)
            if df0 is None:
                continue
            cols = df0.columns.tolist()
            mapping = {k: find_col(cols, *v) for k, v in {
                'FilingDate': ['filing date'],
                'TradeDate':  ['trade date'],
                'Ticker':     ['ticker'],
                'InsiderName':['insider name'],
                'Title':      ['title'],
                'TradeType':  ['trade type'],
                'Shares':     ['qty','share'],
                'Price':      ['price']
            }.items()}
            if any(v is None for v in mapping.values()):
                continue
            d = pd.DataFrame({
                'FilingDate':  df0[mapping['FilingDate']],
                'TradeDate':   pd.to_datetime(df0[mapping['TradeDate']]),
                'Ticker':      df0[mapping['Ticker']],
                'InsiderName': df0[mapping['InsiderName']],
                'Title':       df0[mapping['Title']],
                'TradeType':   df0[mapping['TradeType']],
                'Shares':      df0[mapping['Shares']].astype(str).replace(r"[+,]","",regex=True).astype(int),
                'Price':       df0[mapping['Price']].astype(str).replace(r"[\$,]","",regex=True).astype(float),
            })
            d = d[d['TradeType'].str.contains('purchase', case=False, na=False)]
            d['SignalStrength'] = d.apply(calculate_signal_strength, axis=1)
            dfs.append(d)
        if dfs:
            all_dfs.append(pd.concat(dfs, ignore_index=True))
    data = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    st.session_state['data'] = data
    st.success(f"✅ Fetched {len(data)} insider buys.")
else:
    data = st.session_state.get('data', pd.DataFrame())

if data.empty:
    st.info("No data to display. Please refresh.")
    st.stop()

# Display tables
col1, col2 = st.columns((2,1))
col1.markdown("### 📋 All Insider Buys")
col1.dataframe(
    data[['FilingDate','TradeDate','Ticker','InsiderName','Title','Shares','Price','SignalStrength']],
    use_container_width=True
)
col2.markdown("### 🏆 Top 5 by Signal Strength")
col2.dataframe(
    data.nlargest(5,'SignalStrength')[['Ticker','InsiderName','Shares','Price','SignalStrength']],
    use_container_width=True
)

# Cluster analysis & price chart
clusters = detect_clusters(data, days_window=days_window, min_insiders=min_insiders)
if not clusters.empty:
    st.markdown("---")
    st.markdown("## 🔍 Clustered Insider Trading Analysis")
    st.dataframe(clusters.sort_values('ClusterScore', ascending=False), use_container_width=True)
    ticker_choice = st.selectbox("Select ticker for price chart", clusters['Ticker'].unique())

    # Fetch intraday data
    price_df = fetch_intraday_data(ticker_choice, interval)
    if price_df.empty:
        st.warning(f"No price data available for {ticker_choice}.")
    else:
        fig = go.Figure()
        # intraday candlesticks
        fig.add_trace(go.Candlestick(
            x=price_df.index,
            open=price_df['open'], high=price_df['high'],
            low=price_df['low'], close=price_df['close'],
            name='Intraday'
        ))
        # Overlay clusters
        for _, cl in clusters[clusters['Ticker']==ticker_choice].iterrows():
            val = price_df['close'].asof(cl['EndDate'])
            fig.add_trace(go.Scatter(
                x=[cl['EndDate']], y=[val], mode='markers',
                marker=dict(size=8+cl['NumInsiders']*2), name='Cluster'
            ))
        # Buy/sell simulation
        buy_price = st.number_input(f"Enter BUY price for {ticker_choice}", min_value=0.0, step=0.01)
        sell_price = st.number_input(f"Enter SELL price for {ticker_choice}", min_value=0.0, step=0.01)
        if buy_price > 0 and sell_price > 0:
            ret = (sell_price - buy_price) / buy_price * 100
            st.metric("Simulated Net Return", f"{ret:.2f}%")
            fig.add_hline(y=buy_price, line_dash='dash', annotation_text='BUY At', line_color='green')
            fig.add_hline(y=sell_price, line_dash='dash', annotation_text='SELL At', line_color='red')

        st.plotly_chart(fig, use_container_width=True)

# Test Telegram notification unchanged
if st.button("Send Test Notification"):
    msg = f"🚨 TEST ALERT ({datetime.now():%m/%d %I:%M%p}): Sample alert"
    try:
        token = st.secrets['telegram']['bot_token']
        chat_id = st.secrets['telegram']['chat_id']
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
        )
        st.success("✅ Telegram test sent!")
    except Exception as e:
        st.error(f"❌ Telegram error: {e}")

from datetime import datetime

def build_ai_prompt(df):
    today = datetime.now().date()
    today_str = today.strftime("%B %d, %Y")

    # Step 1: Try pulling today's trades
    today_trades = df[df["TradeDate"].dt.date == today]

    # Step 2: If none, find fallback — the most clustered recent date
    if not today_trades.empty:
        selected_trades = today_trades
        header_date = today_str
        fallback_note = ""
    else:
        # Sort by most recent and count clusters
        df_sorted = df.sort_values("TradeDate", ascending=False)
        most_common_date = df_sorted["TradeDate"].dt.date.value_counts().idxmax()
        selected_trades = df[df["TradeDate"].dt.date == most_common_date]
        header_date = most_common_date.strftime("%B %d, %Y")
        fallback_note = f"\n📌 *Note: No insider trades were found for {today_str}. Falling back to the most prominent recent cluster on {header_date}.*\n"

    # Step 3: Still nothing? Return graceful error message
    if selected_trades.empty:
        return f"""⚠️ No insider trade data available to generate an AI prompt.
Check your OpenInsider feed, date filters, or refresh the dataset."""

    # Step 4: Begin constructing the AI prompt
    prompt = f"""You are a top-tier equity research analyst at a major hedge fund. Today’s task is to generate a deep-dive investment memo from insider trading data for {header_date}.{fallback_note}

Your job is to take each of the insider purchases below and:
- Explain what the company does in 2–3 sentences.
- Find the next earnings date. Highlight it if it is within 3 weeks.
- Determine if the insider has made similar purchases in the past and what happened to the stock after.
- Analyze the technicals: RSI, 50-day and 200-day MAs, MACD if relevant.
- Evaluate the conviction behind the trade based on role (e.g., CEO vs Director), size, and price paid.
- Suggest **entry and exit points** for a swing trade or long-term position.
- Comment on any significant price/volume patterns in the last month.
- Use precise financial language, no fluff.

Insider trade summary:"""

    # Step 5: Loop through each selected trade and format its section
    for _, row in selected_trades.iterrows():
        ticker = row["Ticker"]
        insider = row["Insider"]
        title = row["Title"]
        shares = int(row["Shares"])
        price = float(row["Price"])
        total_value = round(shares * price, 2)
        trade_date = row["TradeDate"].strftime("%B %d, %Y")

        prompt += f"""

---
📈 **{ticker}**
- 🗓️ **Trade Date:** {trade_date}
- 🧑‍💼 **Insider:** {insider} — *{title}*
- 💵 **Shares Purchased:** {shares:,} @ ${price:.2f}
- 🧾 **Total Trade Value:** ${total_value:,.2f}

🔬 Analysis Instructions for {ticker}:
1. What does this company do? Summarize in 2–3 lines.
2. When is the next earnings call? If within 21 days, flag it.
3. Has this insider made any recent trades? If yes, how did the stock respond?
4. Review 6-month price chart. Are there any technical patterns (breakouts, consolidations, gaps)?
5. Evaluate conviction: role, trade size, and whether it was above/below current price.
6. Check RSI, 50-day, 200-day moving averages. Mention if it's overbought/oversold or showing a crossover.
7. Recommend a **good entry price** and a **target exit price** based on current momentum, valuation, and technical levels.

"""

    prompt += f"""\nPlease return a bullet-point summary for each stock with cited data and a bolded final investment opinion: **Buy**, **Watch**, or **Avoid**."""
    return prompt
