# Continue website tasks in normal Chrome

The owner's report was a compound command: open apple.com and add a specific
model, capacity and colour to the cart. Opening the URL succeeded, then the model
asked the owner how to automate adding the item and claimed only general navigation
was available. The task was subsequently cancelled; that was not a cart or purchase.

`windows.open_chrome_url` remains a URL handoff to the owner's regular Chrome,
not an attachment of the Strict Browser's Playwright tools. After a successful
handoff, both cloud planner adapters now propose one registered fresh window-list
observation before making another model call. This read still passes through the
runner, permission engine, audit, budget and cancellation. A failed handoff,
simulation, unavailable/blocked observation tool or a newer observation does not
inject that read. No HWND is guessed and no action is approved by this helper.
Simple literal site commands retain their local no-cloud recipe.

The shared planner instructions explain how to continue through the observed
Chrome window, focus, UIA inspection and native element actions. They distinguish
the user's browser from the separately managed browser.* context. UIA now includes
Hyperlink, RadioButton, CheckBox and ComboBox in addition to the existing roles,
so product links and variant controls are no longer discarded. Password/invisible
controls, stale targets, ambiguous matches and the limits of 96 returned elements,
67000 metadata bytes and depth 32 retain their checks.

The owner should be asked for missing decisions such as store country, delivery
region, unavailable variants and budget, not instructions on how to click.
The planner must re-observe selected variants and cart contents and cannot count
an invocation receipt as a verified cart. Add-to-cart/checkout commands are treated
as purchase tasks by the existing in-chat cloud-data review. This is not an
effect approval. Ordinary navigation still opens without confirmation.

Checkout/payment is a separate financial commitment. This change does not add a
purchase-specific approval contract or enable CRITICAL tools. The instructions
require the exact observed item, seller, total/currency and delivery terms before
approval; where the current tools cannot bind that purchase safely, the owner
must complete checkout. Never claim a paid order from a click or a prepared cart.

## Validation

Unit tests cover native product roles, password/invisible filtering, cloud review
classification, both providers' post-handoff read and its simulation/failure/blocked
boundaries. The opt-in native test uses a synthetic loopback shop and the real
Windows worker, runner and permission engine in normal Chrome:

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_chrome_cart.py --run-windows -q
```

It follows a product Hyperlink, selects 256 GB and Burgundy radio buttons, invokes
Add to Bag, and independently checks the local server's exact cart parameters and
a freshly observed completion title. Its Place order button is disabled. It uses
no model, credentials, public retailer, payment method or real order. Native result
on the owner's Windows 11 machine: 1 passed. This fixture does not verify Apple
stock, prices, regional availability or end-to-end model-driven purchasing.
