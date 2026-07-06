# Q759: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral)
- Entrypoint: User sends native tokens to the DirectDepositV1 receive() path or routes ERC4626 wrapping through ContractOwner helper flows.
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Deposits must credit no more value than the helper actually transfers into protocol custody.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).
