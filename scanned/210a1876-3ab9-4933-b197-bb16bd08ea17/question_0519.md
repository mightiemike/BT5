# Q519: Stale or double-applied directDepositV1Address

## Question
Can attacker-controlled sequencing make core/contracts/DirectDepositV1.sol / creditDeposit(...) consume stale directDepositV1Address or apply the same directDepositV1Address transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/DirectDepositV1.sol / creditDeposit(...)
- Entrypoint: User sends native tokens to the DirectDepositV1 receive() path or routes ERC4626 wrapping through ContractOwner helper flows.
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale directDepositV1Address before all related state is finalized.
- Invariant to test: Helper-assisted asset wrapping and direct-deposit flows must not strand value, overcredit balances, or allow cross-token confusion.
- Expected HackenProof impact: Critical/High: logic attack where direct deposit or wrapping creates protocol credit without real assets.
- Fast validation: Test repeated creditDeposit() and receive() flows around wrappedNative and ERC4626 wrapping to assert no stale approvals or double-credit paths exist.
