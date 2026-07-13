# Q1449: ExitTierWithDelegation owner binding against min lock requirement

## Question
Can position owner enter through `msgServer.ExitTierWithDelegation` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, validator exchange rate, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then exit immediately after validator slash changes token-per-share, causing `min lock requirement` to diverge so the invariant `rounding cannot let owner recover more stake than the position held` fails and the attacker can partial-exit below min lock and keep a dust position that still earns rewards, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ExitTierWithDelegation
- Entrypoint: Cosmos SDK MsgExitTierWithDelegation transaction
- Attacker controls: Owner, PositionId, Amount, validator exchange rate, position shares
- Exploit idea: partial-exit below min lock and keep a dust position that still earns rewards by testing the owner binding angle against `min lock requirement` during `exit immediately after validator slash changes token-per-share`, with specific focus on position primary store.
- Invariant to test: rounding cannot let owner recover more stake than the position held; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test comparing staking shares and token balances before and after ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
