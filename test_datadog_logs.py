import requests

resp = requests.post(
    "https://http-intake.logs.us5.datadoghq.com/api/v2/logs",
    headers={
        "DD-API-KEY": "a9120c590c8d84acb68d47d20f887fe2",
        "Content-Type": "application/json"
    },
    json=[
        {
            "message": "uday-test-log",
            "service": "llm-health-guardian-api",
            "ddsource": "manual"
        }
    ]
)

print(resp.status_code, resp.text)
