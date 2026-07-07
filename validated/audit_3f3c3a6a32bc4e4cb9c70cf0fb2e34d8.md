### Title
Unchecked `transferFrom` Return Value Enables Silent USDC.e Drain Without USDC Compensation — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the boolean return value. If the USDC token returns `false` instead of reverting (silent failure), the function continues to withdraw USDC.e from the DDA and transfer it to the caller — without the caller ever having provided USDC. Any unprivileged caller can trigger this path.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no access control modifier. Its intended logic is an atomic swap: receive USDC from the caller into the DDA, then release USDC.e from the DDA back to the caller. [1](#0-0) 

The critical sequence is:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← return value discarded
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [2](#0-1) 

The `IIERC20Base` interface declares `transferFrom` as returning `bool`: [3](#0-2) 

The `ERC20Helper.safeTransferFrom` wrapper — which correctly checks the return value — is available and used elsewhere in the codebase: [4](#0-3) 

But `replaceUsdcEWithUsdc` bypasses this wrapper and calls `.transferFrom()` directly, discarding the return value. The subsequent `withdraw` and `safeTransfer` calls are not gated on the success of the inbound transfer.

---

### Impact Explanation

If the USDC token at the hardcoded address returns `false` on a failed transfer (e.g., insufficient allowance, zero balance, or a non-standard bridged token variant that does not revert), the function silently proceeds to:

1. Call `DirectDepositV1.withdraw(usdcE)` — transferring the DDA's entire USDC.e balance to `ContractOwner`.
2. Call `safeTransfer(msg.sender, balance)` — forwarding that USDC.e to the attacker.

The attacker receives USDC.e without having provided any USDC. The DDA's USDC.e balance is zeroed; the DDA receives no USDC. The subaccount owner's collateral is silently drained.

**Exact corrupted asset delta**: USDC.e balance of the targeted DDA (`directDepositV1Address[subaccount]`) is reduced by `balance`; the attacker gains `balance` USDC.e at zero cost.

---

### Likelihood Explanation

The function is `external` with no access control — any address on Ink mainnet (chain ID 57073) can call it for any registered subaccount. The exploitability depends on whether the specific USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` or reverts on failure. Standard Circle USDC reverts, which would prevent exploitation in the common case. However, bridged or wrapped token variants on non-mainnet chains sometimes return `false`. The code is structurally broken regardless, and the risk is non-zero given the hardcoded address is on a relatively new chain (Ink mainnet).

---

### Recommendation

Replace the raw `.transferFrom()` call with the existing `safeTransferFrom` wrapper from `ERC20Helper`, which checks the return value and reverts on failure:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [5](#0-4) 

This ensures the function reverts atomically if the inbound USDC transfer fails, preventing any USDC.e from being released.

---

### Proof of Concept

1. Identify a subaccount whose DDA (`directDepositV1Address[subaccount]`) holds a non-zero USDC.e balance.
2. As an attacker with zero USDC allowance granted to `ContractOwner`, call:
   ```solidity
   ContractOwner.replaceUsdcEWithUsdc(subaccount)
   ```
3. If USDC's `transferFrom` returns `false` (silent failure):
   - `DirectDepositV1.withdraw(usdcE)` executes, moving USDC.e to `ContractOwner`.
   - `safeTransfer(msg.sender, balance)` executes, sending USDC.e to the attacker.
4. Attacker receives the DDA's full USDC.e balance; the DDA receives no USDC. [1](#0-0)

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

**File:** core/contracts/DirectDepositV1.sol (L6-12)
```text
interface IIERC20Base {
    function transfer(address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 amount) external returns (bool);
}
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
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
    }
```
