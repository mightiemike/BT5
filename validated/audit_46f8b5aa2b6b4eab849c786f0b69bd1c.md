### Title
`MsgTierRedelegate` Hardcodes `LastKnownBonded = true` Without Verifying Destination Validator Is Bonded, Enabling Bonus Accrual During Unbonded Periods — (`File: x/tieredrewards/keeper/msg_server.go`)

---

### Summary

After a tier position is redelegated via `MsgTierRedelegate`, the module unconditionally sets `LastKnownBonded = true` on the position regardless of whether the destination validator is actually bonded. Because `processEventsAndClaimBonus` uses `LastKnownBonded` as the starting bonded state for lazy event