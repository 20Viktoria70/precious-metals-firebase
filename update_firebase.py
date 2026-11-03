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

end_date = date.today()
start_date = end_date - timedelta(days=30)

start_str = start_date.strftime("%d-%m-%Y")
end_str = end_date.strftime("%d-%m-%Y")

print(f"Період завантаження: {start_str} - {end_str}")


# ============================================================
# 3. НАЛАШТУВАННЯ ДЖЕРЕЛ ДАНИХ
# ============================================================

JM_URL = (
    "https://matthey.com/products-and-markets/"
    "pgms-and-circularity/pgm-management"
    "?p_p_id=jm_metal_price_portlet_JmMetalPricePortlet"
    "&p_p_lifecycle=2&p_p_state=normal"
    "&p_p_mode=view&p_p_cacheability=cacheLevelPage"
)

KITCO_API = "https://www.kitco.com/api/proxy-historical-data"

JM_METALS = {
    "Platinum": "Pt",
    "Palladium": "Pd",
    "Rhodium": "Rh",
    "Iridium": "Ir",
    "Ruthenium": "Ru"
}

KITCO_METALS = {
    "AU": "Gold_Kitco",
    "AG": "Silver_Kitco",
    "PT": "Platinum_Kitco",
    "PD": "Palladium_Kitco"
}

INDICATORS = [
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


# ============================================================
# 4. ДОПОМІЖНА ФУНКЦІЯ ДЛЯ ЧИСЛОВИХ ЗНАЧЕНЬ
# ============================================================

def to_number(value):

    if isinstance(value, dict):
        value = value.get("USD")

    if value is None:
        return None

    return pd.to_numeric(
        str(value).replace(",", ""),
        errors="coerce"
    )


# ============================================================
# 5. ЗАВАНТАЖЕННЯ ДАНИХ JOHNSON MATTHEY
# ============================================================

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
        headers={
            "User-Agent": "Mozilla/5.0"
        },
        timeout=60
    )

    response.raise_for_status()

    try:
        result = response.json()
    except ValueError:
        raise ValueError(
            "Johnson Matthey повернув не JSON: "
            + response.text[:300]
        )

    if not isinstance(result, dict) or "url" not in result:
        raise ValueError(
            "У відповіді Johnson Matthey немає посилання на CSV."
        )

    csv_url = result["url"]

    df = pd.read_csv(csv_url, header=1)

    if "Date" not in df.columns:
        raise ValueError(
            "У файлі Johnson Matthey відсутня колонка Date."
        )

    df["Date"] = pd.to_datetime(
        df["Date"],
        errors="coerce"
    )

    df = df.dropna(subset=["Date"])

    for metal in JM_METALS:

        if metal in df.columns:
            df[metal] = df[metal].apply(to_number)

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
# 6. ЗАВАНТАЖЕННЯ ДАНИХ KITCO
# ============================================================

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

        try:
            year_data = response.json()
        except ValueError:
            raise ValueError(
                "Kitco повернув не JSON: "
                + response.text[:300]
            )

        if not isinstance(year_data, dict):
            raise ValueError(
                "Невідомий формат відповіді Kitco."
            )

        raw_data = year_data.get("data", [])

        # Іноді дані можуть бути JSON-рядком.

        if isinstance(raw_data, str):

            try:
                raw_data = json.loads(raw_data)
            except ValueError:
                raise ValueError(
                    "Kitco повернув текст замість списку даних: "
                    + raw_data[:300]
                )

        # Обробка списку або словника.

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
                f"Невідомий тип даних Kitco: {type(raw_data)}"
            )

        for day_info in data_items:

            if not isinstance(day_info, dict):
                continue

            day = pd.to_datetime(
                day_info.get("date", day_info.get("Date")),
                errors="coerce"
            )

            if pd.isna(day):
                continue

            if day < start or day > end:
                continue

            hubs = day_info.get("hubs", {})

            if not isinstance(hubs, dict):
                continue

            hub_data = hubs.get("London", {})

            if not isinstance(hub_data, dict):
                continue

            row = {"Date": day}

            for code, column in KITCO_METALS.items():

                row[column] = to_number(
                    hub_data.get(code)
                )

            records.append(row)

    df = pd.DataFrame(records)

    if df.empty:
        raise ValueError(
            "Kitco не повернув даних за вибраний період."
        )

    df = df.drop_duplicates(subset=["Date"])
    df = df.sort_values("Date")

    print("Kitco:", len(df), "записів")

    return df


# ============================================================
# 7. ЗАВАНТАЖЕННЯ ДАНИХ YAHOO FINANCE
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
            print("Немає даних для", ticker)
            continue

        close = data["Close"]

        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        temp = close.reset_index()
        temp.columns = ["Date", column]

        result.append(temp)

    if not result:
        raise ValueError(
            "Yahoo Finance не повернув ціни металів."
        )

    df = result[0]

    for temp in result[1:]:
        df = df.merge(
            temp,
            on="Date",
            how="outer"
        )

    df["Date"] = pd.to_datetime(
        df["Date"]
    ).dt.tz_localize(None)

    print("Yahoo Finance (метали):", len(df), "записів")

    return df


# ============================================================
# 8. ЗАВАНТАЖЕННЯ ЕКОНОМІЧНИХ ІНДЕКСІВ
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
            print("Немає даних для", ticker)
            continue

        close = data["Close"]

        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        temp = close.reset_index()
        temp.columns = ["Date", column]

        result.append(temp)

    if not result:
        raise ValueError(
            "Не вдалося отримати економічні індекси."
        )

    df = result[0]

    for temp in result[1:]:
        df = df.merge(
            temp,
            on="Date",
            how="outer"
        )

    df["Date"] = pd.to_datetime(
        df["Date"]
    ).dt.tz_localize(None)

    print("Економічні індекси:", len(df), "записів")

    return df


# ============================================================
# 9. БЕЗПЕЧНЕ ЗАВАНТАЖЕННЯ ДЖЕРЕЛ
# ============================================================

print("\nПочаток завантаження даних...\n")

source_frames = []
successful_sources = []

# Johnson Matthey

try:

    df_jm = download_jm_metals(start_str, end_str)

    source_frames.append(df_jm)
    successful_sources.append("Johnson Matthey")

except Exception as error:

    print("ПОПЕРЕДЖЕННЯ: Johnson Matthey недоступний.")
    print("Причина:", error)


# Kitco

try:

    df_kitco = download_kitco(start_str, end_str)

    source_frames.append(df_kitco)
    successful_sources.append("Kitco")

except Exception as error:

    print("ПОПЕРЕДЖЕННЯ: Kitco недоступний.")
    print("Причина:", error)


# Yahoo Finance — метали

try:

    df_yahoo = download_yahoo_metals(
        start_date,
        end_date
    )

    source_frames.append(df_yahoo)
    successful_sources.append("Yahoo Finance Metals")

except Exception as error:

    print("ПОПЕРЕДЖЕННЯ: Yahoo Finance (метали) недоступний.")
    print("Причина:", error)


# Yahoo Finance — економічні індекси

try:

    df_indices = download_economic_indices(
        start_date,
        end_date
    )

    source_frames.append(df_indices)
    successful_sources.append("Yahoo Finance Indices")

except Exception as error:

    print("ПОПЕРЕДЖЕННЯ: економічні індекси недоступні.")
    print("Причина:", error)


# Перевірка наявності даних

if not source_frames:
    raise RuntimeError(
        "Не вдалося завантажити дані з жодного джерела."
    )


# ============================================================
# 10. ОБ'ЄДНАННЯ ДАНИХ
# ============================================================

df_all = source_frames[0].copy()

for frame in source_frames[1:]:

    df_all = df_all.merge(
        frame,
        on="Date",
        how="outer"
    )

df_all["Date"] = pd.to_datetime(
    df_all["Date"],
    errors="coerce"
)

df_all = df_all.dropna(subset=["Date"])
df_all = df_all.sort_values("Date").reset_index(drop=True)


# ============================================================
# 11. ФОРМУВАННЯ ЄДИНОЇ ТАБЛИЦІ ПОКАЗНИКІВ
# ============================================================

def get_column(df, name):

    if name in df.columns:
        return df[name]

    return pd.Series(
        index=df.index,
        dtype="float64"
    )


# Золото: Kitco, потім Yahoo Finance.

df_all["Gold"] = get_column(
    df_all, "Gold_Kitco"
).combine_first(
    get_column(df_all, "Gold_Yahoo")
)


# Срібло: Kitco, потім Yahoo Finance.

df_all["Silver"] = get_column(
    df_all, "Silver_Kitco"
).combine_first(
    get_column(df_all, "Silver_Yahoo")
)


# Платина та паладій: Johnson Matthey, потім Kitco.

for metal in ["Platinum", "Palladium"]:

    df_all[metal] = get_column(
        df_all, f"{metal}_Matthey"
    ).combine_first(
        get_column(df_all, f"{metal}_Kitco")
    )


# Інші метали: Johnson Matthey.

for metal in ["Rhodium", "Iridium", "Ruthenium"]:

    df_all[metal] = get_column(
        df_all, f"{metal}_Matthey"
    )


# Перевірка всіх необхідних показників.

for indicator in INDICATORS:

    if indicator not in df_all.columns:
        df_all[indicator] = pd.NA


df_final = df_all[
    ["Date"] + INDICATORS
].copy()

df_final["Date"] = df_final["Date"].dt.strftime(
    "%Y-%m-%d"
)

print("\nОб'єднані дані:")
print(df_final.tail())

print("\nКількість завантажених записів:", len(df_final))


# ============================================================
# 12. ОНОВЛЕННЯ ІСТОРИЧНИХ ДАНИХ FIREBASE
# ============================================================

print("\nОновлення історичних даних Firebase...")

updated_records = 0

for _, row in df_final.iterrows():

    daily_data = {}

    for indicator in INDICATORS:

        value = row[indicator]

        if pd.notna(value):
            daily_data[indicator] = float(value)

    if daily_data:

        date_key = row["Date"]

        # Оновлюємо лише наявні значення.
        # Старі показники та дати не видаляються.

        historical_ref.child(date_key).update(
            daily_data
        )

        updated_records += 1

print("Оновлено дат:", updated_records)


# ============================================================
# 13. ОНОВЛЕННЯ ПОТОЧНИХ ЦІН
# ============================================================

print("\nОновлення поточних цін...")

current_data = current_ref.get() or {}

for indicator in INDICATORS:

    available_data = df_final.dropna(
        subset=[indicator]
    )

    if not available_data.empty:

        last_row = available_data.iloc[-1]

        current_data[indicator] = {
            "date": last_row["Date"],
            "value": float(last_row[indicator])
        }

current_ref.update(current_data)

print("Поточні ціни оновлено.")


# ============================================================
# 14. ОНОВЛЕННЯ МЕТАДАНИХ
# ============================================================

metadata_ref.update({
    "last_update": date.today().isoformat(),
    "indicators": INDICATORS,
    "indicators_count": len(INDICATORS),
    "last_update_records": updated_records,
    "data_sources": successful_sources
})


# ============================================================
# 15. ПЕРЕВІРКА РЕЗУЛЬТАТУ
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

print("\nДжерела, які надали дані:")
print(successful_sources)

print("\nОНОВЛЕННЯ FIREBASE ЗАВЕРШЕНО!")
