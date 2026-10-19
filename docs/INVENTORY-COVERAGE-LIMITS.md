# Inventory scope: confirmed warehouse sums vs companywide availability

The current inventory ledger is authoritative for **explicitly opened
product/location balances only**. A product with no confirmed opening
rows is UNKNOWN, not zero. For **multiple warehouses**, numerical balances
can be summed ONLY if that product has an explicitly approved canonical
unit (e.g. all quantities in PCS). Historic free-text records without
a confirmed unit are shown per warehouse but their combined total is
UNKNOWN / INCOMPARABLE; it is never guessed from their numerical values.

Therefore Telegram and Mini App stock views show "Təsdiqlənmiş anbarlar
üzrə cəm" only when the verified quantities share a canonical unit or
when exactly one opening warehouse is reported. An approved unit still
does NOT establish that all company warehouses have opening balances.
Do not call any partial number "total company stock", "available to sell",
"no stock elsewhere", or a complete stocktake.

Reservations, when present, reduce **available stock** but do not reduce
physical **on-hand stock** until consumed. Neither this view nor catalog
product discovery silently infers availability from text or images.
No new warehouse or unit is inferred by any AI response.

A full registered list of active warehouses, explicit product units
and warehouse-opening completeness certificate is still an independent
release requirement (#40). Changes to product quantity semantics must
have an administrator-approved migration, not a default of zero.
