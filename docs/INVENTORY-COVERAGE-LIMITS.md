# Inventory scope: confirmed warehouse sums vs companywide availability

The current inventory ledger is authoritative for **explicitly opened
product/location balances only**. A product with no confirmed opening rows
is UNKNOWN, not zero. Once some locations are opened, a sum of those
locations is meaningful but it is NOT evidence that all company
warehouses are included, because a complete approved warehouse registry
and unit-of-measure coverage check is not yet implemented.

Therefore Telegram and Mini App stock views now label the number
"Təsdiqlənmiş anbarlar üzrə cəm" (sum of confirmed locations) and explicitly
warn that other locations may be unknown. Do not call this "total
company stock", "available to sell", "no stock elsewhere", or a
complete stocktake.

Reservations, when present, reduce **available stock** but do not reduce
physical **on-hand stock** until consumed. Neither this view nor catalog
product discovery silently infers availability from text or images.
No new warehouse or unit is inferred by any AI response.

A full registered list of active warehouses, explicit product units
and warehouse-opening completeness certificate is still an independent
release requirement (#40). Changes to product quantity semantics must
have an administrator-approved migration, not a default of zero.
