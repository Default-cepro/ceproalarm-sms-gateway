

def parse_response(response: str, expected: str) -> str:
    if expected in response:
        return "ONLINE"

    return "INOPERATIVO"