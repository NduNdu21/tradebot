"""Connect to the OANDA practice API and print the account balance."""
import oandapyV20
import oandapyV20.endpoints.accounts as accounts

from src.config import load_config


def main():
    cfg = load_config()
    client = oandapyV20.API(access_token=cfg["token"], environment=cfg["env"])

    response = client.request(accounts.AccountSummary(cfg["account_id"]))
    account = response["account"]
    print(f"Balance: {account['balance']} {account['currency']}")


if __name__ == "__main__":
    main()
