import asyncio
import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path


class EmailReportService:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        from_address: str = "",
        use_tls: bool = True,
        use_ssl: bool = False,
        timeout_seconds: int = 20,
    ):
        self.smtp_host = str(smtp_host or "").strip()
        self.smtp_port = int(smtp_port)
        self.smtp_username = str(smtp_username or "").strip()
        self.smtp_password = smtp_password or ""
        self.from_address = str(from_address or "").strip()
        self.use_tls = bool(use_tls)
        self.use_ssl = bool(use_ssl)
        self.timeout_seconds = int(timeout_seconds)

    async def send_report(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        attachment_paths: list[str],
    ) -> dict:
        return await asyncio.to_thread(
            self._send_report_sync,
            recipients=recipients,
            subject=subject,
            body=body,
            attachment_paths=attachment_paths,
        )

    def _send_report_sync(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        attachment_paths: list[str],
    ) -> dict:
        clean_recipients = [str(r).strip() for r in recipients if str(r).strip()]
        if not clean_recipients:
            raise ValueError("No hay destinatarios configurados para el reporte por correo.")
        if not self.smtp_host:
            raise ValueError("SMTP host no configurado.")
        if not self.from_address:
            raise ValueError("Remitente (from_address) no configurado.")

        msg = EmailMessage()
        msg["From"] = self.from_address
        msg["To"] = ", ".join(clean_recipients)
        msg["Subject"] = subject
        msg.set_content(body)

        attached = 0
        for path_value in attachment_paths:
            path = Path(str(path_value or "").strip())
            if not path.is_file():
                continue

            content_type, _ = mimetypes.guess_type(path.name)
            if content_type:
                maintype, subtype = content_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"

            with path.open("rb") as f:
                payload = f.read()

            msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=path.name)
            attached += 1

        if self.use_ssl:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout_seconds) as server:
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout_seconds) as server:
                if self.use_tls:
                    server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)

        return {
            "sent_to": len(clean_recipients),
            "attachments": attached,
            "subject": subject,
        }
