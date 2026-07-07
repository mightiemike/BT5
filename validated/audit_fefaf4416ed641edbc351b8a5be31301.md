### Title
Unchecked `transferFrom` Return Value Enables USDC-e Theft Without Providing USDC - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(...)` directly without checking its boolean return value. The protocol already provides `ERC20Helper.safeTransferFrom` for exactly this purpose, but it is not used here. If the USDC token returns `false` on failure instead of reverting, the function continues execution, withdrawing USDC-e from the DDA and sending it to the caller — without the caller ever providing USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is a permissionless function (only gated by `block.chainid == 57073`) that implements a token swap: the caller provides USDC and receives USDC-e held in a `DirectDepositV1` (DDA) contract.

The swap logic is:
1. Read USDC-e balance of the DDA.
2. Pull USDC from `msg.sender` into the DDA via `transferFrom`.
3. Withdraw USDC-e from the DDA to `ContractOwner`.
4. Send USDC-e to `msg.sender` via `safeTransfer`.

Step 2 uses a raw, unchecked call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value (`bool`) is silently discarded. Steps 3 and 4 execute unconditionally regardless of whether step 2 succeeded. [1](#0-0) 

By contrast, the protocol's own `ERC20Helper` library wraps both `transfer` and `transferFrom` with full return-value checks and is used elsewhere in the same file (e.g., line 618 uses `safeTransfer` for the outbound USDC-e leg of the same swap): [2](#0-1) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink (chain ID 57073) returns `false` on a failed `transferFrom` rather than reverting — a behavior exhibited by several non-standard ERC-20 tokens — an attacker with zero USDC allowance can:

- Call `replaceUsdcEWithUsdc(subaccount)` for any DDA holding USDC-e.
- The `transferFrom` silently fails (returns `false`, not checked).
- `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes, pulling all USDC-e to `ContractOwner`.
- `safeTransfer(msg.sender, balance)` sends all USDC-e to the attacker.

The attacker drains the entire USDC-e balance of the targeted DDA at zero cost. The DDA receives no USDC in return. The subaccount owner loses their collateral. [3](#0-2) 

---

### Likelihood Explanation

The function is callable by any unprivileged address on Ink (chain ID 57073) — there is no `onlyOwner` or similar guard. The only precondition is that a DDA for the target subaccount exists and holds a nonzero USDC-e balance. The exploitability depends on whether the specific USDC deployment at that address returns `false` or reverts on failure. Given that this is a bridged/wrapped USDC variant on a newer chain, its exact revert-vs-return-false behavior warrants verification. The structural vulnerability (missing return-value check) is unconditional. [4](#0-3) 

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's existing `safeTransferFrom` wrapper from `ERC20Helper`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper` is already imported and used in `ContractOwner` via `using ERC20Helper for IERC20Base`. [5](#0-4) 

---

### Proof of Concept

1. A DDA exists for `subaccount` holding `N` USDC-e (balance > 0).
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` is called. If USDC returns `false` (no revert), execution continues.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` USDC-e from DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` sends `N` USDC-e to the attacker.
6. Attacker receives `N` USDC-e; DDA receives 0 USDC. Net: attacker stole `N` USDC-e. [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-610)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
```

**File:** core/contracts/ContractOwner.sol (L614-619)
```text
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
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
