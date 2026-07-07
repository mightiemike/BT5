### Title
Unchecked Raw `transferFrom` in `replaceUsdcEWithUsdc` Enables usdcE Drain Without USDC Payment — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` as a raw, unchecked call. If the USDC token at the hardcoded Ink-chain address returns `false` on a failed transfer (rather than reverting), the function silently skips the USDC inflow but still withdraws usdcE from the target DDA and sends it to the caller. Any unprivileged caller on chain ID 57073 can drain usdcE from any DDA that holds a balance.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public, permissionless function (no `onlyOwner` modifier) gated only by `block.chainid == 57073`. Its intended flow is:

1. Pull `balance` of USDC from `msg.sender` into the DDA.
2. Withdraw usdcE from the DDA to `ContractOwner`.
3. Forward usdcE to `msg.sender`.

Step 1 uses a raw interface call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

The return value is never checked. The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom`, which low-level-calls the token and requires `success && (data.length == 0 || abi.decode(data, (bool)))`:

```solidity
function safeTransferFrom(...) internal {
    (bool success, bytes memory data) = address(self).call(...);
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
``` [2](#0-1) 

The `using ERC20Helper for IERC20Base` directive is active in `ContractOwner`, so `safeTransferFrom` is available but was not used here. [3](#0-2) 

Steps 2 and 3 execute unconditionally after the unchecked call:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [4](#0-3) 

`DirectDepositV1.withdraw` uses its own internal `safeTransfer` and will succeed as long as the DDA holds usdcE. [5](#0-4) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink chain returns `false` on a failed `transferFrom` (e.g., zero allowance, insufficient balance) rather than reverting, an attacker with zero USDC allowance can:

- Call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds usdcE.
- The USDC pull silently fails (returns `false`, not checked).
- The DDA's entire usdcE balance is transferred to the attacker.
- Net result: attacker receives usdcE for free; the DDA is drained.

The corrupted asset delta is the full usdcE balance of the targeted DDA, transferred to an arbitrary caller with no USDC consideration.

---

### Likelihood Explanation

The function is callable by any address on Ink chain (chain ID 57073) with no access control. The only precondition is that a DDA for the target subaccount exists and holds a non-zero usdcE balance. Whether the specific USDC deployment at that address returns `false` or reverts on failure determines exploitability; the code provides no protection either way, and the inconsistency with the rest of the codebase indicates this was an oversight.

---

### Recommendation

Replace the raw call with the safe wrapper already available via `ERC20Helper`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This mirrors the pattern used everywhere else in the codebase and ensures a failed transfer always reverts, preventing the subsequent usdcE withdrawal from executing.

---

### Proof of Concept

1. A DDA exists for `subaccount` on Ink chain with `usdcE.balanceOf(dda) = X > 0`.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, dda, X)` returns `false` (no revert).
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers `X` usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, X)` sends `X` usdcE to the attacker.
6. Attacker receives `X` usdcE; DDA is drained; no USDC was paid.

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L616-619)
```text
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
