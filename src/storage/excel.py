import pandas as pd


def load_devices(path: str) -> pd.DataFrame:
    return pd.read_excel(path)


def save_devices(df: pd.DataFrame, path: str):
    df.to_excel(path, index=False)
