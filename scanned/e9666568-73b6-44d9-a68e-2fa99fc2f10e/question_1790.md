# Q1790: Global accumulator bleed across users or products

## Question
Can attacker-controlled actions through core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance) push a shared accumulator such as fees, insurance, funding, utilization, queue counters, or collected balances in a way that later lets the attacker redeem, avoid, or shift value that should belong to another user or product?

## Target
- File/function: core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Track every shared accumulator touched before and after core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance), then interleave two users or two products and see whether the second actor can benefit from state that the first actor should have exclusively paid for or earned.
- Invariant to test: Shared protocol accumulators must remain correctly partitioned by user, product, pool, and request semantics.
- Expected HackenProof impact: Critical/High: loss of funds or logic attack through value bleed across shared accounting buckets.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.
