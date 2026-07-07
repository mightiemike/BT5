### Title
Unchecked `transferFrom` Return Value Enables USDCe Drain Without USDC Payment — (`File: core/contracts/ContractOwner.sol`)

---

### Summary
`ContractOwner.replaceUsdcEWithUsdc` performs a raw `IERC20Base.transferFrom` call without checking its return value. If the USDC token at the hardcoded address returns `false` on a failed transfer (rather than reverting), the function continues execution, withdrawing USDCe from the target `DirectDepositV1` and sending it to the caller — without the caller ever providing USDC.

---

### Finding Description
The function `replaceUsdcEWithUsdc` is designed as a token-swap helper: the caller provides USDC and receives USDCe held in a `DirectDepositV1` address. The critical step is:

```solidity
// core/contracts/ContractOwner.sol, line 616
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
```

The `transferFrom` call is a direct interface call — not routed through `ERC20Helper.safeTransferFrom`. Its return value is silently discarded. If the call returns `false` (e.g., insufficient allowance, insufficient balance, or a non-reverting token), execution falls through to the `withdraw` and `safeTransfer` calls, which send USDCe to the caller unconditionally.

Contrast this with every other transfer in the protocol, which correctly uses `ERC20Helper.safeTransferFrom`:

```solidity
// core/contracts/libraries/ERC20Helper.sol, lines 29–41
(bool success, bytes memory data) = address(self).call(...);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
```

The `replaceUsdcEWithUsdc` function is the sole site in the production codebase that bypasses this safe wrapper for a `transferFrom`.

---

### Impact Explanation
An unprivileged caller on chain ID 57073 (Ink) can call `replaceUsdcEWithUsdc` for any `subaccount` that has a deployed `DirectDepositV1` with a non-zero USDCe balance. If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed transfer (e.g., caller has zero allowance or zero balance), the caller receives the full USDCe balance of the DDA for free. The corrupted asset delta is the entire USDCe balance of the targeted `DirectDepositV1`, transferred to the attacker with no USDC consideration.

---

### Likelihood Explanation
The function is `external` with no access modifier — any address can call it on the target chain. The exploitability is conditional on the USDC token at the hardcoded address returning `false` rather than reverting on failure. Many ERC20 tokens (including older USDC deployments and USDT-style tokens) exhibit this behavior. The hardcoded address is a deployment-specific token whose exact revert behavior on Ink chain is not guaranteed to match mainnet USDC. The attack requires no privileged access, no governance capture, and no leaked keys.

---

### Recommendation
Replace the raw `transferFrom` call with the protocol's own `ERC20Helper.safeTransferFrom`, consistent with every other transfer site in the codebase:

```solidity
// Replace:
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// With:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
// (using the ERC20Helper library already imported and applied via `using ERC20Helper for IERC20Base`)
```

---

### Proof of Concept

1. Attacker identifies a `subaccount` with a deployed `DirectDepositV1` holding non-zero USDCe balance on chain ID 57073.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` returns `false` (no revert) because allowance is zero.
4. Return value is not checked; execution continues.
5. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers all USDCe from the DDA to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends all USDCe to the attacker.
7. Attacker receives full USDCe balance; no USDC was ever transferred.

**Root cause line:** [1](#0-0) 

**Safe wrapper bypassed:** [2](#0-1) 

**Surrounding exploit context:** [3](#0-2)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-41)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```
