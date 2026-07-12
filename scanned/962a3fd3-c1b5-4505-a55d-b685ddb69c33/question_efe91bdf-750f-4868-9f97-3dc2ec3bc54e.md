[File: 'cmd/chain-maind/main.go -> Scope: High. Cross-module invariant break between staking, slashing, distribution, bank, NFT, NFT-transfer, supply, inflation, or tieredrewards corrupts balances, shares, ownership, rewards, or escrow state with direct fund-loss impact.'] [Function: ExitTierWithDelegation / ExitWithFullDelegation / reconcileAmountFromShares / MeetsMinLockRequirement] Can a delegator bypass the tier minimum lock requirement in ExitTierWithDelegation by exploiting rounding in reconcile

### Citations

**File:** cmd/chain-maind/main.go (L1-17)
```go
package main

import (
