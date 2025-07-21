import requests
from bs4 import BeautifulSoup
import pandas as pd

url = "http://openinsider.com/latest-insider-trading"
headers = {"User-Agent": "Mozilla/5.0"}

# Step 1: Fetch the page
response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, "html.parser")

# Step 2: Find the insider trading table
table = soup.find("table", class_="tinytable")

# Step 3: Extract table rows
rows = table.find_all("tr")[1:]  # Skip header row

data = []
for row in rows:
    cols = row.find_all("td")
    if len(cols) >= 10:
        date = cols[0].text.strip()
        ticker = cols[2].text.strip()
        insider = cols[4].text.strip()
        title = cols[5].text.strip()
        trade_type = cols[6].text.strip()
        shares = cols[7].text.strip().replace(',', '')
        price = cols[8].text.strip().replace('$', '')

        try:
            shares = int(shares)
            price = float(price)
        except:
            continue

        # Only keep large buys or key executive roles
        if "Buy" in trade_type and (shares > 10000 or any(role in title for role in ["CEO", "Chair", "Director", "President"])):
            data.append([date, ticker, insider, title, shares, price])

# Step 4: Output results
df = pd.DataFrame(data, columns=["Date", "Ticker", "Insider", "Title", "Shares", "Price"])
print(df.head())
df.to_csv("insider_trades.csv", index=False)
