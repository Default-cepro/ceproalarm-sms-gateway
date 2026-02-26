def _clean_cell(value) -> str:
    return str(value or "").strip().lower()


def validate_devices(df, commands_config):
    """
    Valida que cada marca/modelo del Excel exista en el JSON de comandos.
    """
    valid_indexes = []
    invalid_devices = []

    for index, row in df.iterrows():
        phone = _clean_cell(row.get("Telefono"))
        brand = _clean_cell(row.get("Marca"))
        model = _clean_cell(row.get("Modelo"))

        if not phone:
            invalid_devices.append((index, "Telefono vacio"))
            continue

        if not brand or not model:
            invalid_devices.append((index, "Marca/Modelo vacio o no interpretable"))
            continue

        if brand not in commands_config:
            invalid_devices.append((index, f"Marca no soportada: {brand}"))
            continue

        if model not in commands_config[brand]:
            invalid_devices.append((index, f"Modelo no soportado: {brand} {model}"))
            continue

        valid_indexes.append(index)

    return valid_indexes, invalid_devices
