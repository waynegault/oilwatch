# Supplier Connectors Guide

This document describes the supplier-specific connectors for automated and manual quote collection.

---

## Overview

OilWatch now includes **supplier-specific connectors** for each of the 7 discovered heating oil suppliers. Each connector is tailored to the supplier's quote mechanism:

| Supplier | Connector | Automation Level | Price Found |
|----------|-----------|-----------------|-------------|
| **ValueOils** | `valueoils_auto` | ✅ Automated (web scraping) | **155.80p/L** (£1635.90 inc VAT) |
| **HomeFuels Direct** | `homefuels_direct_auto` | ⚠️ Semi-automated (price extraction) | Varies |
| **Oilfast Insch** | `oilfast_manual` | Manual (phone/email/form) | - |
| **Rix** | `rix_manual` | Manual (phone/email/quote tool) | - |
| **Regency Oils** | `regency_oils_manual` | Manual (phone/instant quote) | - |
| **Scottish Fuels** | `scottish_fuels_manual` | Manual (account required) | - |
| **Brogan Fuels** | `brogan_fuels_manual` | Manual (redirects to Scottish Fuels) | - |

---

## Automated Connectors

### ValueOils (`valueoils_auto`)

**Status:** ✅ Fully Automated  
**Mechanism:** Web scraping of Quick Quote prices  
**Price:** 155.50-155.80 pence/litre (Ex VAT)  
**VAT Rate:** 5% (domestic heating oil)

**How it works:**
1. Fetches the supplier's Aberdeenshire page
2. Extracts price using regex patterns
3. Calculates total with 5% VAT for domestic customers
4. Returns structured quote result

**Configuration:**
```json
{
  "connector_type": "valueoils",
  "connector_config": {
    "quote_url": "https://www.valueoils.com/regions/scotland/aberdeenshire/"
  }
}
```

### HomeFuels Direct (`homefuels_direct_auto`)

**Status:** ⚠️ Semi-Automated  
**Mechanism:** Web scraping of tier-based pricing  
**Price:** ~132 pence/litre (UK average for 900L+)

**How it works:**
1. Fetches the pricing page
2. Looks for tier-based pricing (500L, 900L+)
3. Extracts price per litre
4. Falls back to manual contact if extraction fails

---

## Manual Connectors

These connectors provide structured contact information and quote instructions.

### Oilfast Insch (`oilfast_manual`)

**Contact:**
- Phone (Insch): 01464 631 835
- Phone (General): 03302 320 104
- Email: insch@oilfast.co.uk
- Enquiry Form: https://oilfast.co.uk/depot/insch/

**Notes:** Local Aberdeenshire supplier, response within 24 hours

### Rix (`rix_manual`)

**Contact:**
- Aberdeen Depot: 01224 418294
- General: 0800 542 4207
- Email (Aberdeen): montsales@rix.co.uk
- Online Quote: https://www.rix.co.uk/fuels/heating-oil

**Notes:** Online quote tool available ("Get your fuel quote here")

### Regency Oils (`regency_oils_manual`)

**Contact:**
- Phone: 01542 832327
- Website: https://www.regencyoils.com

**Services:**
- Monthly budget plan
- Automatic top-up service
- Community buying group service

### Scottish Fuels (`scottish_fuels_manual`)

**Contact:**
- Phone (General): 0345 300 8844
- Phone (Aberdeenshire): 01224 213 132
- Website: https://scottishfuels.co.uk

**Notes:** Online quote generator requires account registration

### Brogan Fuels (`brogan_fuels_manual`)

**Contact:**
- Phone: 0345 300 8844
- Email: domestic@brogans.co.uk
- Website: https://www.brogans.co.uk

**Notes:** Part of Scottish Fuels (same company)

---

## Telephone Quote Script Tool

Use the `phone-script` command to generate call scripts for manual quote collection:

```powershell
# Generate telephone scripts for all suppliers
.venv\Scripts\python -m oilwatch.cli phone-script `
  --quantity-liters 1000 `
  --postcode "AB21 0YA" `
  --name "Your Name" `
  --address "Your Address"

# Export to JSON for tracking
.venv\Scripts\python -m oilwatch.cli phone-script `
  --postcode "AB21 0YA" `
  --name "Your Name" `
  --output data/quote-calls.json
```

**Output includes:**
- Quick reference card with all phone numbers
- Individual call scripts for each supplier
- Quote recording checklist
- JSON export for tracking results

---

## File Structure

```
oilwatch/connectors/suppliers/
├── __init__.py              # Connector registry
├── homefuels_direct.py      # HomeFuels Direct connector
├── valueoils.py             # ValueOils connector
├── oilfast.py               # Oilfast Insch connector
├── rix.py                   # Rix connector
├── regency_oils.py          # Regency Oils connector
├── scottish_fuels.py        # Scottish Fuels connector
├── brogan_fuels.py          # Brogan Fuels connector
└── telephone.py             # Telephone quote script tool
```

---

## How Connectors Work

### Connector Selection

When `quote-all` is called, the system:

1. **Checks website domain** → Matches to supplier-specific connector
2. **Falls back to connector_type** → Uses generic `manual`, `price_page`, or `http_form`
3. **Defaults to manual** → If no match found

### Quote Result Structure

```python
QuoteResult(
    supplier_id=1,
    supplier_name="ValueOils",
    observed_at="2026-03-23T18:00:00",
    quantity_liters=1000,
    status="ok",  # or "manual_action_required", "error"
    price_per_liter=1.558,
    total_price=1635.90,
    currency="GBP",
    source="valueoils_auto",
    notes="Price extracted from Quick Quote...",
    raw_payload={...}
)
```

---

## Adding New Supplier Connectors

To add a new supplier-specific connector:

1. **Create connector file** in `oilwatch/connectors/suppliers/`:
   ```python
   from oilwatch.connectors.base import BaseConnector
   from oilwatch.models import QuoteResult, OrderResult

   class NewSupplierConnector(BaseConnector):
       def quote(self, supplier, quantity_liters, context):
           # Implement quote logic
           return QuoteResult(...)
       
       def place_order(self, supplier, quantity_liters, agreed_price, context):
           # Implement order logic
           return OrderResult(...)
   ```

2. **Register in `__init__.py`**:
   ```python
   from oilwatch.connectors.suppliers.new_supplier import NewSupplierConnector

   def get_supplier_connector(website: str):
       connectors = {
           "example.com": NewSupplierConnector(),
           # ...
       }
       for domain, connector in connectors.items():
           if domain in website.lower():
               return connector
       return None
   ```

3. **Test the connector**:
   ```powershell
   .venv\Scripts\python -m oilwatch.cli quote <supplier_id>
   ```

---

## Current Pricing (from automated collection)

| Date | Supplier | Price/L | Total (1000L) | VAT |
|------|----------|---------|---------------|-----|
| 2026-03-23 | ValueOils | £1.558 | £1635.90 | 5% inc |

**Note:** Prices fluctuate daily. Run `quote-all` regularly for current prices.

---

## Next Steps

1. **Improve HomeFuels Direct connector** - Add browser automation for reliable price extraction
2. **Add Rix quote tool integration** - Investigate the online quote tool API
3. **Add price history tracking** - Store historical prices for trend analysis
4. **Add price alerts** - Notify when prices drop below threshold
