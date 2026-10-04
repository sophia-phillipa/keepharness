"""DeepSeek BYOK: private credential file and read-only account checks."""

from pathlib import Path

import httpx

from agent_service.errors import UserMessageError

API = "https://api.deepseek.com"


def key_file(state):
    return Path(state) / "deepseek.key"


def store_key(state, token):
    if not isinstance(token, str) or not 16 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise UserMessageError("Invalid API token.")
    path = key_file(state)
    temp = path.with_suffix(".tmp")
    temp.write_text(token)
    temp.chmod(0o600)
    temp.replace(path)


async def fetch_balance(key_path):
    """The raw ``/user/balance`` answer for the stored key, or None when it cannot be read."""
    try:
        token = Path(key_path).read_text().strip()
        async with httpx.AsyncClient(
            base_url=API, headers={"Authorization": "Bearer " + token}, timeout=10, trust_env=False
        ) as client:
            response = await client.get("/user/balance")
            return response.json() if response.status_code == 200 else None
    except (OSError, ValueError, httpx.HTTPError):
        return None


def balance_summary(answer):
    """The balance fields the quota panel shows; amounts stay the strings the provider sent."""
    infos = answer.get("balance_infos") if isinstance(answer, dict) else None
    if not isinstance(infos, list) or not infos:
        return None
    fields = ("currency", "total_balance", "granted_balance", "topped_up_balance")
    infos = [
        info
        for info in infos
        if isinstance(info, dict)
        and all(isinstance(info.get(field), str) and info[field].strip() for field in fields)
    ]
    if not infos:
        return None
    return {
        "account_active": answer.get("is_available") is True,
        "balances": [
            {
                "currency": info["currency"],
                "total": info["total_balance"],
                "granted": info["granted_balance"],
                "topped_up": info["topped_up_balance"],
            }
            for info in infos
        ],
    }


async def check(state):
    path = key_file(state)
    if not path.exists():
        raise UserMessageError("Add your DeepSeek key in the assistant.")
    async with httpx.AsyncClient(
        base_url=API,
        headers={"Authorization": "Bearer " + path.read_text().strip()},
        timeout=15,
        trust_env=False,
    ) as client:
        try:
            response = await client.get("/models")
            if response.status_code in (401, 403):
                raise UserMessageError("DeepSeek key rejected. Check the credential with the provider.")
            response.raise_for_status()
            models = {
                m["id"]: ["configured", "none", "low", "high", "max"]
                for m in response.json()["data"]
            }
            balance_response = await client.get("/user/balance")
            balance = balance_response.json() if balance_response.status_code == 200 else None
            return {
                "authenticated": True,
                "models": models,
                "balance": balance,
                "model_source": "DeepSeek API /models",
            }
        except (httpx.HTTPError, KeyError):
            raise UserMessageError("Could not query the DeepSeek API. Try again.") from None
