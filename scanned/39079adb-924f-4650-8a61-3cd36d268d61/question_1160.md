# Q1160: Nominal-versus-realized asset mismatch

## Question
Can core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount) trust a nominal amount, preview amount, claimed amount, or signed amount that diverges from the assets actually transferred or the balances actually settled, creating unbacked credit or underpaid liabilities?

## Target
- File/function: core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount)
- Entrypoint: User sends native tokens to the DirectDepositV1 receive() path or routes ERC4626 wrapping through ContractOwner helper flows.
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Compare the user-controlled nominal amount against the realized token movement, internal balance delta, and downstream settlement effect caused by core/contracts/DirectDepositV1.sol / safeTransfer(IIERC20Base self, address to, uint256 amount), especially around fees, wrappers, and non-standard token behavior.
- Invariant to test: Internal accounting must track realized asset movement and must not mint credit or settle liabilities from nominal amounts alone.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through overcredit, underpayment, or hidden insolvency.
- Fast validation: Use a fee-on-transfer, rebasing, or callback-capable token in a Hardhat test and compare helper balances versus protocol credit after creditDeposit(...).
