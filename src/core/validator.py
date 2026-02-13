def validate_devices(df, commands_config):
    """
    Valida que cada marca y modelo del Excel
    exista en el JSON de comandos.
    """

    valid_indexes = []
    invalid_devices = []

    for index, row in df.iterrows():
        brand = str(row["Marca"]).lower()
        model = str(row["Modelo"]).lower()

        if brand not in commands_config:
            invalid_devices.append(
                (index, f"Marca no soportada: {brand}")
            )
            continue

        if model not in commands_config[brand]:
            invalid_devices.append(
                (index, f"Modelo no soportado: {brand} {model}")
            )
            continue

        valid_indexes.append(index)

    return valid_indexes, invalid_devices
