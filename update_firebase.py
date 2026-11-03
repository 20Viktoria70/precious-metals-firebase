# ============================================================
# ЛАБОРАТОРНА РОБОТА №3
# Автоматичне оновлення цін дорогоцінних металів у Firebase
# ============================================================

import os
import json
import requests
import pandas as pd
import yfinance as yf
import firebase_admin

from datetime import date, timedelta
from io import StringIO
from firebase_admin import credentials, db


# ============================================================
# 1. НАЛАШТУВАННЯ FIREBASE
# ============================================================

DATABASE_URL = os.environ["FIREBASE_DATABASE_URL"]
credentials_json = os.environ["FIREBASE_CREDENTIALS_JSON"]

credentials_dict = json.loads(credentials_json)
cred = credentials.Certificate(credentials_dict)

if not firebase_admin._apps:
    firebase_admin.initialize_app(
        cred,
        {"databaseURL": DATABASE_URL}
    )

historical_ref = db.reference("historical_prices")
current_ref = db.reference("current_prices")
metadata_ref = db.reference("metadata")


# ============================================================
# 2. НАЛАШТУВАННЯ ПЕРІОДУ ЗАВАНТАЖЕННЯ
# ============================================================

# Завантажуємо останні 30 днів.
# Це дозволяє врахувати затримки та виправлення даних.

end_date = date.today()
start_date = end_date - timedelta(days=30)

start_str = start_date.strftime("%d-%m-%Y")
end_str = end_date.strftime("%d-%m-%Y")

print(f"Період завантаження: {start_str} - {end_str}")


# ============================================================
# 3. ЗАВАНТАЖЕННЯ ДАНИХ JOHNSON MATTHEY
# ============================================================

JM_URL = (
    "https://matthey.com/products-and-markets/"
    "pgms-and-circularity/pgm-management"
    "?p_p_id=jm_metal_price_portlet_JmMetalPricePortlet"
    "&p_p_lifecycle=2&p_p_state=normal"
    "&p_p_mode=view&p_p_cacheability=cacheLevelPage"
)

JM_METALS = {
    "Platinum": "Pt",
    "Palladium": "Pd",
    "Rhodium": "Rh",
    "Iridium": "Ir",
    "Ruthenium": "Ru"
}


def download_jm_metals(start_date, end_date):

    print("Завантаження даних Johnson Matthey...")

    response = requests.post(
        JM_URL,
        data={
            "startDate": start_date,
            "endDate": end_date,
            "interval": "daily",
            "metals": ",".join(JM_METALS.values())
        },
        timeout=60
    )

    response.raise_for_status()

    result = response.json()
    csv_url = result["url"]

    df = pd.read_csv(csv_url, header=1)

    df["Date"] = pd.to_datetime(
        df["Date"],
        errors="coerce"
    )

    df = df.dropna(subset=["Date"])

    for metal in JM_METALS:
        if metal in df.columns:
            df[metal] = pd.to_numeric(
                df[metal].astype(str).str.replace(",", ""),
                errors="coerce"
            )

    df = df.rename(columns={
        metal: f"{metal}_Matthey"
        for metal in JM_METALS
        if metal in df.columns
    })

    df = df.drop_duplicates(subset=["Date"])
    df = df.sort_values("Date")

    print("Johnson Matthey:", len(df), "записів")

    return df


# ============================================================
# 4. ЗАВАНТАЖЕННЯ ДАНИХ KITCO
# ============================================================

KITCO_API = "https://www.kitco.com/api/proxy-historical-data"

KITCO_METALS = {
    "AU": "Gold_Kitco",
    "AG": "Silver_Kitco",
    "PT": "Platinum_Kitco",
    "PD": "Palladium_Kitco"
}


def download_kitco(start_date, end_date):

    print("Завантаження даних Kitco...")

    start = pd.to_datetime(start_date, dayfirst=True)
    end = pd.to_datetime(end_date, dayfirst=True)

    records = []

    for year in range(start.year, end.year + 1):

        response = requests.get(
            KITCO_API,
            params={"year": year},
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=60
        )

        response.raise_for_status()

        year_data = response.json()

                raw_data = year_data.get("data", [])

        # Kitco може повертати дані у вигляді списку
        # або словника з датами як ключами.

        if isinstance(raw_data, dict):

            data_items = []

            for key, value in raw_data.items():

                if isinstance(value, dict):
                    day_info = value.copy()
                    day_info.setdefault("date", key)
                    data_items.append(day_info)

        elif isinstance(raw_data, list):

            data_items = raw_data

        else:
            raise ValueError(
                f"Невідомий формат відповіді Kitco: {type(raw_data)}"
            )

        for day_info in data_items:

            if not isinstance(day_info, dict):
                continue

            day = pd.to_datetime(
                day_info.get("date"),
                errors="coerce"
            )

            if pd.isna(day) or day < start or day > end:
                continue

            hub_data = (
                day_info.get("hubs", {}).get("London", {})
            )

            row = {"Date": day}

            for code, column in KITCO_METALS.items():

                value = hub_data.get(code)

                if isinstance(value, dict):
                    value = value.get("USD")

                row[column] = pd.to_numeric(
                    value,
                    errors="coerce"
                )

            records.append(row)

            day = pd.to_datetime(
                day_info.get("date"),
                errors="coerce"
            )

            if pd.isna(day) or day < start or day > end:
                continue

            hub_data = (
                day_info.get("hubs", {}).get("London", {})
            )

            row = {"Date": day}

            for code, column in KITCO_METALS.items():

                value = hub_data.get(code)

                if isinstance(value, dict):
                    value = value.get("USD")

                row[column] = pd.to_numeric(
                    value,
                    errors="coerce"
                )

            records.append(row)

    df = pd.DataFrame(records)

    if df.empty:
        raise ValueError("Kitco не повернув даних за вибраний період.")

    df = df.drop_duplicates(subset=["Date"])
    df = df.sort_values("Date")

    print("Kitco:", len(df), "записів")

    return df


# ============================================================
# 5. ЗАВАНТАЖЕННЯ ДАНИХ З YAHOO FINANCE
# ============================================================

def download_yahoo_metals(start_date, end_date):

    print("Завантаження цін металів Yahoo Finance...")

    tickers = {
        "GC=F": "Gold_Yahoo",
        "SI=F": "Silver_Yahoo"
    }

    result = []

    for ticker, column in tickers.items():

        data = yf.download(
            ticker,
            start=start_date,
            end=end_date + timedelta(days=1),
            progress=False,
            auto_adjust=False
        )

        if data.empty:
            continue

        close = data["Close"]

        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        temp = close.reset_index()
        temp.columns = ["Date", column]

        result.append(temp)

    if not result:
        raise ValueError("Yahoo Finance не повернув ціни металів.")

    df = result[0]

    for temp in result[1:]:
        df = df.merge(temp, on="Date", how="outer")

    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)

    print("Yahoo Finance (метали):", len(df), "записів")

    return df


# ============================================================
# 6. ЗАВАНТАЖЕННЯ ЕКОНОМІЧНИХ ІНДЕКСІВ
# ============================================================

def download_economic_indices(start_date, end_date):

    print("Завантаження економічних індексів...")

    tickers = {
        "^GSPC": "SP500",
        "^FTSE": "FTSE100"
    }

    result = []

    for ticker, column in tickers.items():

        data = yf.download(
            ticker,
            start=start_date,
            end=end_date + timedelta(days=1),
            progress=False,
            auto_adjust=False
        )

        if data.empty:
            continue

        close = data["Close"]

        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        temp = close.reset_index()
        temp.columns = ["Date", column]

        result.append(temp)

    if not result:
        raise ValueError("Не вдалося отримати економічні індекси.")

    df = result[0]

    for temp in result[1:]:
        df = df.merge(temp, on="Date", how="outer")

    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)

    print("Економічні індекси:", len(df), "записів")

    return df


# ============================================================
# 7. ОБ'ЄДНАННЯ ДАНИХ
# ============================================================

print("\nПочаток завантаження даних...\n")

# Завантаження Johnson Matthey
try:
    df_jm = download_jm_metals(start_str, end_str)
    jm_available = True

except Exception as error:
    print("ПОПЕРЕДЖЕННЯ: Johnson Matthey недоступний.")
    print("Причина:", error)

    df_jm = pd.DataFrame(columns=[
        "Date",
        "Platinum_Matthey",
        "Palladium_Matthey",
        "Rhodium_Matthey",
        "Iridium_Matthey",
        "Ruthenium_Matthey"
    ])

    jm_available = False


# Завантаження інших джерел
df_kitco = download_kitco(start_str, end_str)
df_yahoo = download_yahoo_metals(start_date, end_date)
df_indices = download_economic_indices(start_date, end_date)

# Об'єднання всіх джерел за датою

df_all = (
    df_jm
    .merge(df_kitco, on="Date", how="outer")
    .merge(df_yahoo, on="Date", how="outer")
    .merge(df_indices, on="Date", how="outer")
)

df_all = df_all.sort_values("Date").reset_index(drop=True)


# ============================================================
# 8. ФОРМУВАННЯ ЄДИНОЇ ТАБЛИЦІ ПОКАЗНИКІВ
# ============================================================

# Для кожного показника встановлюємо пріоритет джерел.
# combine_first використовує наступне джерело,
# якщо в попередньому значення відсутнє.

df_all["Gold"] = (
    df_all.get("Gold_Kitco", pd.Series(index=df_all.index, dtype=float))
    .combine_first(
        df_all.get("Gold_Yahoo", pd.Series(index=df_all.index, dtype=float))
    )
)

df_all["Silver"] = (
    df_all.get("Silver_Kitco", pd.Series(index=df_all.index, dtype=float))
    .combine_first(
        df_all.get("Silver_Yahoo", pd.Series(index=df_all.index, dtype=float))
    )
)

for metal in ["Platinum", "Palladium"]:
    df_all[metal] = (
        df_all.get(f"{metal}_Matthey", pd.Series(index=df_all.index, dtype=float))
        .combine_first(
            df_all.get(f"{metal}_Kitco", pd.Series(index=df_all.index, dtype=float))
        )
    )

for metal in ["Rhodium", "Iridium", "Ruthenium"]:
    df_all[metal] = df_all.get(
        f"{metal}_Matthey",
        pd.Series(index=df_all.index, dtype=float)
    )

indicators = [
    "Gold",
    "Silver",
    "Platinum",
    "Palladium",
    "Rhodium",
    "Iridium",
    "Ruthenium",
    "SP500",
    "FTSE100"
]

# Залишаємо тільки потрібні показники

df_final = df_all[["Date"] + indicators].copy()

df_final["Date"] = pd.to_datetime(
    df_final["Date"]
).dt.strftime("%Y-%m-%d")

print("\nОб'єднані дані:")
print(df_final.tail())


# ============================================================
# 9. ОНОВЛЕННЯ ІСТОРИЧНИХ ДАНИХ У FIREBASE
# ============================================================

print("\nОновлення історичних даних Firebase...")

updated_records = 0

for _, row in df_final.iterrows():

    daily_data = {}

    for indicator in indicators:

        value = row[indicator]

        if pd.notna(value):
            daily_data[indicator] = float(value)

    if daily_data:

        date_key = row["Date"]

        # update() додає або оновлює лише наявні поля.
        # Старі значення інших показників не видаляються.

        historical_ref.child(date_key).update(daily_data)

        updated_records += 1

print("Оновлено дат:", updated_records)


# ============================================================
# 10. ОНОВЛЕННЯ ПОТОЧНИХ ЦІН
# ============================================================

print("\nОновлення поточних цін...")

current_data = current_ref.get() or {}

for indicator in indicators:

    available_data = df_final.dropna(subset=[indicator])

    if not available_data.empty:

        last_row = available_data.iloc[-1]

        current_data[indicator] = {
            "date": last_row["Date"],
            "value": float(last_row[indicator])
        }

current_ref.update(current_data)

print("Поточні ціни успішно оновлено.")


# ============================================================
# 11. ОНОВЛЕННЯ МЕТАДАНИХ
# ============================================================

metadata_ref.update({
    "last_update": date.today().isoformat(),
    "indicators": indicators,
    "indicators_count": len(indicators),
    "last_update_records": updated_records,
    "data_sources": (
    ["Johnson Matthey"] if jm_available else []
) + [
    "Kitco",
    "Yahoo Finance"
]
})


# ============================================================
# 12. ПЕРЕВІРКА РЕЗУЛЬТАТУ
# ============================================================

print("\nПеревірка Firebase:")

print(
    "Кількість історичних дат:",
    len(historical_ref.get() or {})
)

print(
    "Кількість поточних показників:",
    len(current_ref.get() or {})
)

print("\nПоточні ціни:")

for indicator, info in current_data.items():
    print(
        indicator,
        "—",
        info["value"],
        "дата:",
        info["date"]
    )

print("\nОНОВЛЕННЯ FIREBASE ЗАВЕРШЕНО!")
