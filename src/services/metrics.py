class Metrics:
    def __init__(self):
        self.success = 0
        self.errors = 0
        self.unsupported = 0
        self.inoperative = 0

    def summary(self):
        total_processed = self.success + self.errors

        success_rate = (
            (self.success / total_processed) * 100
            if total_processed > 0
            else 0
        )

        return {
            "success": self.success,
            "errors": self.errors,
            "unsupported": self.unsupported,
            "inoperative": self.inoperative,
            "success_rate": round(success_rate, 2),
        }
