Looking at the `x/tieredrewards` module, I need to find an analog to the "deactivated entity's cycle-tracking state variable gets out of sync with the contract's advancing counter" bug class.

The key mechanism to examine is the lazy validator event replay system: `pos.LastEventSeq` tracks the last processed event for a position's current validator, and `processEventsAndClaimBonus` uses it to query `getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)`. Each validator maintains its own independent, monotonically increasing event sequence.

Let me trace the `MsgTierRedelegate` flow carefully: