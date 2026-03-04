import urllib.request
import urllib.error
import urllib.parse
import json

# URL of the deployed Azure Function
URL = "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api/validate"

# The test payload
data = {
    "narrative": (
        "The Senior Responsible Owner Delivery Confidence Assessment remains Amber "
        "because a recent schedule review identified a fifty-seven-day slip."
    ),
    "project_name": "Sellafield",
    "period": "UNKNOWN"
}

# Encode the payload as JSON bytes
body = json.dumps(data).encode("utf-8")

# Prepare the HTTP request
req = urllib.request.Request(
    url=URL,
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST"
)

print(f"Sending POST request to {URL}...")
print("Payload:")
print(json.dumps(data, indent=2))
print("-" * 50)
print("Waiting for response (this may take 10-15s if the app is waking up from a cold start)...")

try:
    with urllib.request.urlopen(req) as response:
        status = response.status
        resp_body = response.read().decode("utf-8")
        
        print(f"\n✅ SUCCESS (HTTP {status})")
        print("Response from Azure:")
        
        # Try to parse and pretty-print JSON response
        try:
            parsed = json.loads(resp_body)
            print(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            print(resp_body)

except urllib.error.HTTPError as e:
    print(f"\n❌ HTTP ERROR: {e.code} {e.reason}")
    print(e.read().decode("utf-8", errors="ignore"))
except urllib.error.URLError as e:
    print(f"\n❌ URL ERROR: {e.reason}")
except Exception as e:
    print(f"\n❌ UNEXPECTED ERROR: {e}")
