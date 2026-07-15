# Q12570: total deposit gas accounting bypass

## Question

What can an unprivileged user do by submitting transactions, deploying contracts, calling methods, and creating promise receipts so that `total_deposit` in `runtime/runtime/src/config.rs` processes prepaid gas, attached deposit, method args, action lists, and refund paths along the runtime state transition path? User controls prepaid gas, attached deposit, method args, action lists, and refund paths -> `total_deposit` processes that value during transaction conversion, action execution, VM host calls, receipt refunds, and outcome accounting -> the gas bought, gas burnt, gas refunded, and fees charged are conserved across transaction and receipt execution invariant might break -> potential in-scope impact is fee payment bypass, execution beyond protocol limits, or balance manipulation under the NEAR HackenProof scope. Exploit hypothesis: a boundary gas/deposit combination can make this code undercharge execution or over-refund unused gas while still committing state, violating the actual protocol invariant that gas bought, gas burnt, gas refunded, and fees charged are conserved across transaction and receipt execution.

## Target

- File/function: runtime/runtime/src/config.rs:558::total_deposit
- Entrypoint: signed transaction submitted through public RPC and applied by runtime/runtime/src/lib.rs::Runtime::apply
- User-controlled input: prepaid gas, attached deposit, method args, action lists, and refund paths
- Attack path: User controls prepaid gas, attached deposit, method args, action lists, and refund paths -> public entrypoint reaches `total_deposit` -> transaction conversion, action execution, VM host calls, receipt refunds, and outcome accounting handles the value -> invariant failure could produce fee payment bypass, execution beyond protocol limits, or balance manipulation
- Security invariant: gas bought, gas burnt, gas refunded, and fees charged are conserved across transaction and receipt execution
- Expected bounty impact: fee payment bypass, execution beyond protocol limits, or balance manipulation
- Fast validation approach: fuzz gas/deposit/action combinations around protocol limits and assert balance deltas, gas burnt, refunds, and execution status match runtime accounting rules
