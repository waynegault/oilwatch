# Browser Automation & Login Guide

This document describes the browser automation system for automated supplier login and quote collection.

---

## Overview

OilWatch now includes **browser-based connectors** using Playwright that can:

1. ✅ **Automatically log in** to supplier websites using stored credentials
2. ✅ **Navigate quote forms** and extract pricing
3. ✅ **Discover underlying APIs** by intercepting network requests
4. ✅ **Generate secure passwords** for new account registration
5. ✅ **Fall back to HTTP scraping** if browser automation fails

---

## Credentials Management

### Your Email
The account email comes from `config/contact.json` (gitignored) or the
`OILWATCH_EMAIL` environment variable — never from source.

### Password Format
Passwords are generated in the format: `[SupplierName]!{8_random_chars}`

Examples:
- `ScottishFuels!aB3xK9mQ`
- `ValueOils!pL7nM2wX`
- `HomeFuels!qR5tY8zA`

### Credential Storage
Credentials are stored in `config/supplier_credentials.json` (gitignored):

```json
{
  "email": "you@example.com",
  "credentials": {
    "scottish_fuels": {
      "email": "you@example.com",
      "password": "ScottishFuels!aB3xK9mQ",
      "notes": "Auto-generated for Scottish Fuels"
    },
    "valueoils": {
      "email": "you@example.com",
      "password": "ValueOils!pL7nM2wX",
      "notes": "Auto-generated for ValueOils"
    }
  }
}
```

### Credential Commands

```python
from oilwatch.credentials import (
    get_supplier_credentials,
    store_supplier_credentials,
    generate_supplier_password,
)

# Get credentials for a supplier
creds = get_supplier_credentials("scottish_fuels")
print(creds["email"])    # you@example.com
print(creds["password"]) # ScottishFuels!aB3xK9mQ

# Generate and store a new password
password = generate_supplier_password("Scottish Fuels")
# Returns: ScottishFuels!{8_random_chars}

store_supplier_credentials(
    supplier_key="scottish_fuels",
    password=password,
    supplier_name="Scottish Fuels"
)
```

---

## Browser-Based Connectors

### Available Connectors

| Supplier | Connector Class | Status |
|----------|----------------|--------|
| Scottish Fuels | `ScottishFuelsBrowserConnector` | ✅ Ready |
| ValueOils | `ValueOilsBrowserConnector` | ✅ Working |
| HomeFuels Direct | `HomeFuelsDirectBrowserConnector` | ✅ Ready |

### How It Works

1. **Browser Launch**: Playwright launches headless Chromium
2. **Navigation**: Navigate to supplier login/quote page
3. **Login**: Attempt login with stored credentials
4. **Quote Form**: Fill in postcode, quantity, fuel type
5. **Price Extraction**: Extract price from page or API response
6. **Fallback**: If browser fails, fall back to HTTP scraping

### Example Usage

```python
from oilwatch.connectors.suppliers import get_supplier_connector

# Get browser connector for ValueOils
connector = get_supplier_connector("https://www.valueoils.com")

# Get quote
result = connector.quote(
    supplier={"id": 1, "name": "ValueOils", "website": "https://www.valueoils.com"},
    quantity_liters=1000,
    context={"postcode": "AB21 0YA"}
)

print(result.status)           # "ok"
print(result.price_per_liter)  # 1.558
print(result.total_price)      # 1635.90
```

---

## CLI Commands

### Quote with Browser Automation

```powershell
# Get quote from single supplier using browser
.venv\Scripts\python -m oilwatch.cli quote 1 --postcode "AB21 0YA"

# Get quotes from all suppliers (browser connectors auto-selected)
.venv\Scripts\python -m oilwatch.cli quote-all --postcode "AB21 0YA"
```

### API Discovery

Discover underlying APIs used by supplier websites:

```powershell
# Discover APIs on a specific URL
.venv\Scripts\python -m oilwatch.cli api-discover `
  --url "https://scottishfuels.co.uk/quote/" `
  --output "scottish_fuels_api.json"

# Discover APIs for a supplier in the database
.venv\Scripts\python -m oilwatch.cli api-discover `
  --supplier-id 1 `
  --output "supplier1_api.json"
```

Output includes:
- All API endpoints discovered
- Request/response data
- JSON payloads
- Authentication tokens (if any)

---

## Account Registration

For suppliers requiring account registration (e.g., Scottish Fuels):

### Automatic Password Generation
When a connector first attempts login and fails, it will:
1. Generate a password in the format `[SupplierName]!{8_random}`
2. Store credentials in `config/supplier_credentials.json`
3. Return registration instructions

### Manual Registration Steps

1. Run quote command to trigger credential generation:
   ```powershell
   .venv\Scripts\python -m oilwatch.cli quote 1 --postcode "AB21 0YA"
   ```

2. Note the generated password from the output or `config/supplier_credentials.json`

3. Register manually:
   - Go to supplier website
   - Click "Register" or "Sign Up"
   - Use email: `you@example.com`
   - Use the generated password

4. Future quote attempts will automatically log in

---

## API Discovery Results

### ValueOils Discovery Example

The API discovery tool found these endpoints:

| Endpoint | Purpose |
|----------|---------|
| `https://va.tawk.to/v1/widget-settings` | Chat widget config |
| `https://va.tawk.to/v1/session/start` | Chat session init |
| `https://embed.tawk.to/_s/v4/app/...` | Chat language packs |

**Note:** ValueOils uses server-side rendering for prices. The quote form submits via traditional POST, not a separate API.

### Using Discovery for Integration

1. Run API discovery on supplier quote page
2. Look for endpoints containing:
   - `/api/`, `/quote`, `/price`, `/cart`
   - JSON responses with price data
3. Use discovered endpoints to create direct API connectors

---

## Troubleshooting

### Browser Automation Fails

**Symptom:** Quote returns error with browser message

**Solutions:**
1. Check if Playwright browsers are installed:
   ```powershell
   .venv\Scripts\python -m playwright install chromium
   ```

2. Try with visible browser for debugging:
   ```python
   # In connector, change headless=False
   page = await self._setup_browser(headless=False)
   ```

3. Check for CAPTCHA or anti-bot measures

### Login Fails

**Symptom:** "Login failed" or "Invalid credentials"

**Solutions:**
1. Check credentials in `config/supplier_credentials.json`
2. Manually verify login works with stored credentials
3. Supplier may have changed login form - update connector

### Price Extraction Fails

**Symptom:** "Could not extract automated price"

**Solutions:**
1. Run API discovery to find new price endpoints
2. Update price regex patterns in connector
3. Check if supplier changed website layout

---

## Security Notes

### Credential Storage
- Credentials stored in plain text JSON file
- For production, consider:
  - Windows Credential Manager integration
  - Environment variables
  - Encrypted secrets manager

### Browser Automation
- Runs in headless mode by default
- User-agent spoofed to avoid bot detection
- No persistent cookies between sessions

### Rate Limiting
- Add delays between quote requests
- Respect supplier": "supplier websites' terms of service
- Don't hammer APIs with rapid requests

---

## File Structure

```
oilwatch/
├── credentials.py              # Credential management
├── api_discovery.py            # API discovery tool
├── connectors/
│   ├── browser_base.py         # Base browser connector
│   └── suppliers/
│       ├── scottish_fuels_browser.py
│       ├── valueoils_browser.py
│       ├── homefuels_direct_browser.py
│       └── ...
```

---

## Next Steps

1. **Register accounts** on supplier websites using generated passwords
2. **Run API discovery** on each supplier to find direct APIs
3. **Test browser automation** with visible browser for debugging
4. **Add more connectors** for remaining suppliers
5. **Implement order placement** via browser automation

---

## Quick Reference

```powershell
# View stored credentials
cat config/supplier_credentials.json

# Test browser quote (ValueOils)
.venv\Scripts\python -m oilwatch.cli quote 2 --postcode "AB21 0YA"

# Discover APIs
.venv\Scripts\python -m oilwatch.cli api-discover --supplier-id 1

# Generate new password
.venv\Scripts\python -c "from oilwatch.credentials import generate_supplier_password; print(generate_supplier_password('Scottish Fuels'))"
```
