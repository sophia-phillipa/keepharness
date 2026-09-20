"""DeepSeek BYOK: private credential file and read-only account checks."""

from pathlib import Path
import httpx

API = "https://api.deepseek.com"


def key_file(state):
    return Path(state) / "deepseek.key"


def store_key(state, token):
    if (
        not isinstance(token, str)
        or not 16 <= len(token) <= 512
        or any(c.isspace() for c in token)
    ):
        raise ValueError("Token de API inválido.")
    path = key_file(state)
    temp = path.with_suffix(".tmp")
    temp.write_text(token)
    temp.chmod(0o600)
    temp.replace(path)


async def check(state):
    path = key_file(state)
    if not path.exists():
        raise ValueError("Adicione sua chave DeepSeek no assistente.")
    async with httpx.AsyncClient(
        base_url=API,
        headers={"Authorization": "Bearer " + path.read_text().strip()},
        timeout=15,
        trust_env=False,
    ) as client:
        try:
            response = await client.get("/models")
            if response.status_code in (401, 403):
                raise ValueError(
                    "Chave DeepSeek recusada. Confira a credencial no provedor."
                )
            response.raise_for_status()
            models = {
                m["id"]: ["configured", "none", "low", "high", "max"]
                for m in response.json()["data"]
            }
            balance_response = await client.get("/user/balance")
            balance = (
                balance_response.json() if balance_response.status_code == 200 else None
            )
            return {
                "authenticated": True,
                "models": models,
                "balance": balance,
                "model_source": "DeepSeek API /models",
            }
        except (httpx.HTTPError, KeyError) as exc:
            raise ValueError(
                "Não foi possível consultar a API DeepSeek. Tente novamente."
            ) from None
