Looking at the external report's vulnerability class — **arithmetic calculation errors** in a rate/fee computation, specifically:
1. A computed value always strictly exceeding a configured floor
2. A time-period calculation missing an offset/lag

I need to find analogous issues in the BTC light client's difficulty/target arithmetic.

Let me examine the key arithmetic paths.