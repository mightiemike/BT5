Looking at the external report's vulnerability class — **duplicate claim via stale state** — I need to find an analog in this Cosmos SDK chain. The key pattern is: claim rewards for a position, then re-use the pre-claim state to claim again.

The most relevant module is `x/tieredrewards`, a lock-and-earn system. Let me trace the `ClaimTierRewards` message handler carefully.