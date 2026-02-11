from .storage.excel import load_devices, save_devices
from .core.commands import get_command
from .core.parser import parse_response
from .sms.simulator import send_sms
import numpy as np


EXCEL_PATH = "data/localizadores.xlsx"


def main():
    df = load_devices(EXCEL_PATH)
    counter = 0
    for index, row in df.iterrows():
        phone = str(row["Teléfono"])
        brand = str(row["Marca"])
        model = str(row["Modelo"])

        try:
            command_data = get_command(brand, model)
            response = send_sms(brand, model, phone, command_data["command"])
            status = parse_response(response, command_data["expected"])

            df.at[index, "Estado"] = status
            df.at[index, "Error"] = np.nan
            print(f"✅ {phone} actualizado a {status}\n")

        except Exception as e:
            counter = counter + 1
            df.at[index, "Estado"] = "ERROR"
            df.at[index, "Error"] = f"{e}"
            print(f"❌ Error con {phone}: {e}\n")
    
    print(f"Hubieron {counter} inconvenientes (revisar log)")

    save_devices(df, EXCEL_PATH)


if __name__ == "__main__":
    main()
