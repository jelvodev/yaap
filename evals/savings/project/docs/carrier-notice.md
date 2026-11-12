# Carrier notice: new surcharges

Our carrier changes its surcharges with the next price list. Freight must charge the same, so `docs/rules.md`
section 4, `freight/rates.py` and the tests follow these points once we adopt them. Everything not named here stays
as it is.

1. **Fuel** goes from 12 % to 14 % of the base rate after the discount.
2. **Remote area** goes from 4.50 to 4.95, and Schiermonnikoog (postcodes starting with `9166`) is added to the
   remote postcodes.
3. **Oversize**: the longest side may be at most 100 cm instead of 120 cm. The girth limit of 300 cm stays.
4. **Saturday delivery** is also offered in zone 1 (Belgium, Luxembourg, Germany), for 9.00. Zone 0 stays 6.00;
   the other zones still refuse it.
5. **Signature on delivery** is free for an insured parcel (one that has an insurance line); otherwise it stays 1.50.
6. **Insurance** starts above a declared value of 100.00 instead of 500.00. It stays 1 % of the declared value,
   but at least 3.00 instead of 2.00.
