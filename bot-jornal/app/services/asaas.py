"""Cliente da API do Asaas (Pix + estorno).

Docs: https://docs.asaas.com/reference
Autenticacao: header `access_token`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class AsaasError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Asaas HTTP {status}: {body}")
        self.status = status
        self.body = body


@dataclass
class PixCharge:
    asaas_id: str
    value: float
    payload: str            # copia e cola
    encoded_image: str      # PNG em base64
    expiration: datetime | None
    invoice_url: str | None


class AsaasClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        s = get_settings()
        self.api_key = api_key or s.asaas_api_key
        self.base_url = base_url or s.asaas_base_url

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "access_token": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "jornal-bot/1.0",
            },
            timeout=httpx.Timeout(20.0),
        )

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        async with self._client() as client:
            resp = await client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            log.error("Asaas %s %s -> %s %s", method, path, resp.status_code, resp.text)
            raise AsaasError(resp.status_code, resp.text)
        return resp.json() if resp.content else {}

    # ---------------- clientes ----------------

    async def create_customer(
        self, *, name: str, cpf: str, email: str | None, external_ref: str
    ) -> str:
        """Cria (ou reaproveita) um cliente e devolve o id do Asaas."""
        existing = await self._request(
            "GET", "/customers", params={"cpfCnpj": _digits(cpf), "limit": 1}
        )
        if existing.get("data"):
            return existing["data"][0]["id"]

        data = await self._request(
            "POST",
            "/customers",
            json={
                "name": name[:100],
                "cpfCnpj": _digits(cpf),
                "email": email,
                "externalReference": external_ref,
                "notificationDisabled": True,  # quem avisa e o bot, nao o Asaas
            },
        )
        return data["id"]

    # ---------------- cobrancas ----------------

    async def create_pix_charge(
        self,
        *,
        customer_id: str,
        value: float,
        description: str,
        external_ref: str,
        expires_in_minutes: int = 60,
    ) -> PixCharge:
        due = date.today() + timedelta(days=1)
        payment = await self._request(
            "POST",
            "/payments",
            json={
                "customer": customer_id,
                "billingType": "PIX",
                "value": round(value, 2),
                "dueDate": due.isoformat(),
                "description": description[:500],
                "externalReference": external_ref,
            },
        )
        payment_id = payment["id"]
        qr = await self._request("GET", f"/payments/{payment_id}/pixQrCode")

        expiration = None
        if raw := qr.get("expirationDate"):
            expiration = _parse_dt(raw)
        if expiration is None:
            expiration = datetime.now().astimezone() + timedelta(minutes=expires_in_minutes)

        return PixCharge(
            asaas_id=payment_id,
            value=float(payment.get("value", value)),
            payload=qr.get("payload", ""),
            encoded_image=qr.get("encodedImage", ""),
            expiration=expiration,
            invoice_url=payment.get("invoiceUrl"),
        )

    async def get_payment(self, asaas_id: str) -> dict:
        return await self._request("GET", f"/payments/{asaas_id}")

    async def is_paid(self, asaas_id: str) -> bool:
        data = await self.get_payment(asaas_id)
        return data.get("status") in {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}

    async def refund(self, asaas_id: str, *, description: str = "Reembolso solicitado") -> dict:
        return await self._request(
            "POST", f"/payments/{asaas_id}/refund", json={"description": description}
        )


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def _parse_dt(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).astimezone()
        except ValueError:
            continue
    return None


def validate_cpf(raw: str) -> bool:
    """Validacao real de CPF (digitos verificadores)."""
    cpf = _digits(raw)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        total = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        check = (total * 10) % 11
        check = 0 if check == 10 else check
        if check != int(cpf[i]):
            return False
    return True
