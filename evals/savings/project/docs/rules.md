# Tariff rules

How Freight prices a parcel. `freight/rates.py` follows these rules section by section; the tests in `tests/` check
them. Money is whole euro cents.

## 1. Zones

| Zone | Destinations |
|---|---|
| 0 | The Netherlands (`NL`) |
| 1 | Belgium, Luxembourg, Germany (`BE`, `LU`, `DE`) |
| 2 | France, Austria, Denmark, Italy, Spain (`FR`, `AT`, `DK`, `IT`, `ES`) |
| 3 | The other EU member states |
| 4 | The rest of the world |

Country codes are ISO 3166 alpha-2, in any case (`be` is Belgium).

## 2. Chargeable weight

- The volumetric weight is length × width × height in centimetres divided by 5,000, in kilograms.
- The chargeable weight is the larger of the actual and the volumetric weight, rounded **up** to the next half
  kilogram (2.01 kg is charged as 2.5 kg; 2.5 kg stays 2.5 kg). The minimum is 0.5 kg.
- Parcels over 31.5 kg chargeable weight are not accepted.

## 3. Base rates

The weight bands are up to and including 2, 5, 10, 20 and 31.5 kg. A parcel of exactly 5 kg is in the 5 kg band.

| Zone | ≤ 2 kg | ≤ 5 kg | ≤ 10 kg | ≤ 20 kg | ≤ 31.5 kg |
|---|---|---|---|---|---|
| 0 | 6.95 | 7.95 | 9.95 | 14.50 | 19.95 |
| 1 | 9.50 | 11.50 | 15.95 | 22.50 | 29.95 |
| 2 | 13.50 | 16.95 | 22.50 | 31.95 | 42.50 |
| 3 | 16.95 | 21.50 | 28.95 | 41.50 | 55.95 |
| 4 | 24.95 | 34.50 | 49.95 | 74.50 | 99.95 |

## 4. Surcharges

Each surcharge is a separate line of the quote.

1. **Fuel**: 12 % of the base rate (after the discount of section 5 is subtracted), on every parcel.
2. **Remote area**: 4.50 for Dutch postcodes on the Wadden islands, the postcodes starting with `179` (Texel), `8881`
   to `8884` (Terschelling), `8899` (Vlieland) and `9161` to `9164` (Ameland). Only in zone 0.
3. **Oversize**: 15.00 when the longest side is over 120 cm or the girth (length + 2 × width + 2 × height, with the
   longest side as length) is over 300 cm.
4. **Heavy**: 8.00 when the chargeable weight is over 20 kg.
5. **Dangerous goods**: 25.00, accepted in zones 0 to 2 only; a parcel with dangerous goods to zone 3 or 4 is refused.
6. **Saturday delivery**: 6.00, in zone 0 only; elsewhere the option is refused.
7. **Signature on delivery**: 1.50.
8. **Insurance**: when the declared value is over 500.00, 1 % of the declared value, at least 2.00.

## 5. Discounts

- Accounts that send many parcels get a discount on the base rate, by parcels per month: from 100 parcels 5 %, from
  500 parcels 10 %, from 1,000 parcels 15 %.
- Accounts of three years or older get 2 percentage points more.
- The discount is at most 15 % in total.
- A discount applies to the base rate only, never to surcharges.

## 6. Rounding

Every percentage is computed per line and rounded to whole cents, half a cent rounding up (12 % of 7.95 is 0.954,
so 0.95; 5 % of 9.50 is 0.475, so 0.48).

## 7. VAT

21 % over the net total (base rate minus discount plus surcharges) for zones 0 to 3. Zone 4 is export: 0 % VAT.

## 8. Delivery

Working days after pickup: zone 0 one day, zone 1 two, zone 2 three, zone 3 five, zone 4 eight. Saturday delivery
does not change the number of days.

## 9. Quotes

A quote lists the base rate, the discount (as a negative line, only when there is one), each surcharge, the net
total, the VAT and the total, formatted as `EUR 12.34`.
