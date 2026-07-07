Looking at the external report's vulnerability class — **a guard check reverts when a value is zero, but that zero is a valid/expected state when a related parameter is also zero** — I need to find an analogous pattern in Nado where a zero-state check blocks a legitimate operation.

Let me trace through the liquidation finalization path in Nado.