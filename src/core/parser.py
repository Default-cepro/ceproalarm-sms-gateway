def parse_response(response: str, expected: str) -> bool:
    """
    Devuelve True si la respuesta contiene el patrón esperado.
    """
    return (expected or "").strip().lower() in (response or "").strip().lower()
