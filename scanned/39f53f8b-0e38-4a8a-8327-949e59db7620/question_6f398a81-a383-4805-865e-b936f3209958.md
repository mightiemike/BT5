[File: 'File Name: x/chainmain/module.go -> Scope: Critical. NFT module authorization or ownership invariant break lets an attacker mint, transfer, burn, edit, or seize denominations or NFTs they do not control.'] [Function: types.KeyOwner] Can any caller, under the precondition that KeyOwner is called with a non-nil address, a non-empty tokenID, but an empty denomID string, receive a key identical to KeyOwner(address, '', '') because the conditional `if address != nil && len(denomID) > 0` gates both the denomID and tokenID appends, violating the invariant that KeyOwner(addr, '', 'tokenX') must produce a distinct key from KeyOwner(addr, '', ''), causing scoped impact: a prefix iterator scan over KeyOwner(addr, '', '') incorrectly matches entries intended for a specific tokenID, corrupting GetOwner results and enabling a false ownership proof? Proof idea: run a Go unit test asserting bytes.Equal

### Citations

**File:** x/nft/keeper/msg_server.go (L1-20)
```go
// Cronos.com Chain Copyright 2018-present Cronos.com
package keeper

import (
