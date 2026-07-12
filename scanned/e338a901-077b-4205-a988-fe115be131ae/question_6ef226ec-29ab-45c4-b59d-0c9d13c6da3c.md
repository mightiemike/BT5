[File: 'x/nft-transfer/keeper/trace.go -> Scope: Critical. Unprivileged on-chain action causes unintentional withdrawal, draining, loss, theft, burn, or permanent lock of user funds or economically valuable NFTs on Cronos POS Chain.'] [Function: createOutgoingPacket / packet.go loop / BurnNFTUnverified] Can an attacker, under the precondition that they own NFT (classID=ibc/HASH, tokenID=T1) and also own (classID=ibc/HASH, tokenID=T2), trigger the call sequence MsgTransfer(tokenIDs=[T1, T2]) -> createOutgoing

### Citations

**File:** x/nft-transfer/keeper/trace.go (L1-104)
```go
package keeper

import (
	tmbytes
