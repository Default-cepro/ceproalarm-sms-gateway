from .storage.excel import load_devices, save_devices
from .core.commands import get_command, COMMANDS
from .core.parser import parse_response
from .core.validator import validate_devices
from .sms.simulator import send_sms
import numpy as np


EXCEL_PATH = "data/localizadores.xlsx"


def main():
    df = load_devices(EXCEL_PATH)
    valid_indexes, invalid_devices = validate_devices(df, COMMANDS)
    Ecounter = 0
    Invcounter = 0
    
    # Marcar inválidos en Excel
    for index, error_message in invalid_devices:
        Invcounter = Invcounter + 1
        df.at[index, "Error"] = np.nan
        df.at[index, "Estado"] = "NO SOPORTADO"
        df.at[index, "Error"] = error_message
        print(f"⚠ {error_message}")
    
    print("\n")
        
    for index in valid_indexes:
        
        row = df.loc[index]
        
        phone = str(row["Teléfono"])
        brand = str(row["Marca"])
        model = str(row["Modelo"])

        try:
            command_data = get_command(brand, model)
            response = send_sms(brand, model, phone, command_data["command"])
            status = parse_response(response, command_data["expected"])

            df.at[index, "Estado"] = status
            print(f"✅ 0{phone} {brand} {model} actualizado a {status}\n")

        except Exception as e:
            Ecounter = Ecounter + 1
            df.at[index, "Estado"] = "ERROR"
            df.at[index, "Error"] = f"{e}"
            print(f"❌ Error con {phone}: {e}\n")
    
    print(f"Hubieron {Ecounter} inconvenientes (revisar log)\n")
    print(f"Hubieron {Invcounter} Comandos inválidos (revisar log)\n")

    save_devices(df, EXCEL_PATH)


if __name__ == "__main__":
    main()
