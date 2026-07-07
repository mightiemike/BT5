### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without USDC Payment - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` uses a raw, unchecked `IERC20Base.transferFrom` to pull USDC from the caller before releasing usdcE. If the USDC token's `transferFrom` returns `false` instead of reverting (non-standard ERC20 behavior), the function silently continues, withdrawing usdcE from the `DirectDepositV1` address and sending it to the caller — with no USDC ever received.

---

### Finding Description

In `replaceUsdcEWithUsdc`, the function is intended to swap usdcE held in a `DirectDepositV1` (DDA) address for USDC provided by the caller. The sequence is:

1. Pull USDC from `msg.sender` via `transferFrom` (line 616)
2. Withdraw usdcE from the DDA to `ContractOwner` (line 617)
3. Send usdcE to `msg.sender` via `safeTransfer` (line 618)

Step 1 uses a raw, unchecked `transferFrom`:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

The return value is never checked. If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (Ink chain, chainId 57073) returns `false` on failure rather than reverting, execution continues to steps 2 and 3 regardless.

By contrast, the outgoing usdcE transfer on line 618 correctly uses `safeTransfer`, and the contract already imports and uses `ERC20Helper` which provides a `safeTransferFrom` wrapper:

```solidity
function safeTransferFrom(IERC20Base self, address from, address to, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(...);
    require(success && (data.length == 0 || abi.decode(data, (bool))), ERR_TRANSFER_FAILED);
}
``` [2](#0-1) 

The function has **no access control** — it is `external` with only a `block.chainid == 57073` guard, callable by any unprivileged address. [3](#0-2) 

---

### Impact Explanation

An attacker with zero USDC (or with insufficient allowance set) calls `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds usdcE. The `transferFrom` silently returns `false`, the DDA's usdcE is withdrawn to `ContractOwner`, and then sent to the attacker via `safeTransfer`. The attacker receives usdcE without paying USDC. The corrupted asset delta is: usdcE balance of the DDA is reduced to zero; no USDC is deposited in exchange.

---

### Likelihood Explanation

The function is permissionlessly callable by any address on chainId 57073. The only precondition is that a DDA for the target subaccount exists and holds a non-zero usdcE balance. Whether the specific USDC deployment at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` or reverts on failure determines exploitability. Given the inconsistency (safeTransfer used for usdcE but not for USDC), this is a concrete, reachable bug with meaningful asset impact.

---

### Recommendation

Replace the raw `transferFrom` with `safeTransferFrom` from the already-imported `ERC20Helper` library:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [4](#0-3) 

---

### Proof of Concept

1. A DDA exists for `subaccount` and holds `N` usdcE tokens.
2. Attacker calls `replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` returns `false` — no revert.
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` transfers `N` usdcE from DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends `N` usdcE to the attacker.
6. Attacker receives `N` usdcE; DDA is drained; no USDC was paid. [5](#0-4)

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
