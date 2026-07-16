# Legacy normalization drift audit

- Overall status: `legacy_float_compatibility_pass`
- Exact hash match: `False`
- Compatibility match: `True`
- Maximum absolute difference: `9.0949470177292824e-13`
- Maximum relative difference: `1.4782098740360288e-15`
- The legacy files were not modified. Compatibility is not reported as an exact hash pass.

| instance | status | numeric differences | max abs | max rel |
|---|---|---:|---:|---:|
| w30 | legacy_float_compatibility_pass | 6 | 2.2737367544323206e-13 | 1.4782098740360288e-15 |
| w45 | legacy_float_compatibility_pass | 7 | 4.5474735088646412e-13 | 1.2975199743586879e-15 |
| w60 | legacy_float_compatibility_pass | 7 | 9.0949470177292824e-13 | 1.0064057603219346e-15 |

All changed fields are finite floating-point values. Field sets, list order, instance hashes, weights, normalization mode, baseline algorithm/version, baseline policy, and load-floor policy match. Historical stored fitness values replay exactly when evaluated with the frozen specifications. The derived baseline_hash also changes because its hashed payload contains these float values; it is recorded separately and is not treated as an independent scientific field.
