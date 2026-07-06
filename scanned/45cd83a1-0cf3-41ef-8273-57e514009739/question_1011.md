# Q1011: Beneficiary routing default or zero-value coercion

## Question
Can core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount) fall back to a default recipient, default subaccount, zero address, or caller-derived beneficiary in a way that lets the attacker redirect value or settle against the wrong destination without explicitly authorizing it?

## Target
- File/function: core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount)
- Entrypoint: User sends native tokens to the DirectDepositV1 receive() path or routes ERC4626 wrapping through ContractOwner helper flows.
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Force optional recipient fields, empty sendTo values, zero subaccounts, unset isolated mappings, or caller-derived defaults around core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount) and compare who ultimately receives value or state updates.
- Invariant to test: Every value-moving action must resolve to exactly one intended beneficiary and must not silently substitute a different account or recipient.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, transfer, or account mutation through beneficiary confusion.
- Fast validation: Test repeated creditDeposit() and receive() flows around wrappedNative and ERC4626 wrapping to assert no stale approvals or double-credit paths exist.
