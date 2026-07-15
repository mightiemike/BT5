# Q15982: apply distribution current shard refund and balance conservation

## Question

What can an unprivileged user do by submitting transactions, deploying contracts, calling methods, and creating promise receipts so that `apply_distribution_current_shard` in `runtime/runtime/src/global_contracts.rs` processes failed actions, deleted accounts, gas refunds, storage refunds, promise callbacks, and receiver/predecessor account choices along the runtime state transition path? User controls failed actions, deleted accounts, gas refunds, storage refunds, promise callbacks, and receiver/predecessor account choices -> `apply_distribution_current_shard` processes that value during action rollback, refund receipt creation, balance transfer, storage accounting, and outcome finalization -> the NEAR balances, locked balances, storage staking, burnt gas fees, and refunds remain conserved across success and failure paths invariant might break -> potential in-scope impact is stealing/loss of funds, fee payment bypass, or balance manipulation under the NEAR HackenProof scope. Exploit hypothesis: a user-triggered failure path can make this code mint, burn, lock, or refund more tokens than protocol accounting permits, violating the actual protocol invariant that NEAR balances, locked balances, storage staking, burnt gas fees, and refunds remain conserved across success and failure paths.

## Target

- File/function: runtime/runtime/src/global_contracts.rs:189::apply_distribution_current_shard
- Entrypoint: signed transaction submitted through public RPC and applied by runtime/runtime/src/lib.rs::Runtime::apply
- User-controlled input: failed actions, deleted accounts, gas refunds, storage refunds, promise callbacks, and receiver/predecessor account choices
- Attack path: User controls failed actions, deleted accounts, gas refunds, storage refunds, promise callbacks, and receiver/predecessor account choices -> public entrypoint reaches `apply_distribution_current_shard` -> action rollback, refund receipt creation, balance transfer, storage accounting, and outcome finalization handles the value -> invariant failure could produce stealing/loss of funds, fee payment bypass, or balance manipulation
- Security invariant: NEAR balances, locked balances, storage staking, burnt gas fees, and refunds remain conserved across success and failure paths
- Expected bounty impact: stealing/loss of funds, fee payment bypass, or balance manipulation
- Fast validation approach: exercise failure matrices for transfers, function calls, account deletion, staking, and callback refunds while checking total supply and per-account accounting
