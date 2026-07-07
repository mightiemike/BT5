### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Collateral Drain Without Payment - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. If the call returns `false` (silent failure), execution continues: the function withdraws `usdcE` from the `directDepositV1` contract and transfers it to `msg.sender` — without `msg.sender` having provided any USDC. This is a direct analog to the external report's root cause: a call's return status is discarded and execution proceeds with stale/incorrect state.

---

### Finding Description

In `ContractOwner.replaceUsdcEWithUsdc`, the intended flow is:

1. Measure `usdcE` balance held by `directDepositV1`.
2. Pull equivalent `usdc` from `msg.sender` into `directDepositV1` via `transferFrom`.
3. Withdraw `usdcE` from `directDepositV1` to `ContractOwner`.
4. Forward `usdcE` to `msg.sender`.

Step 2 uses a raw `transferFrom` call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value is never checked. [1](#0-0) 

If this call returns `false` (e.g., insufficient allowance, insufficient balance, or a non-reverting ERC-20 implementation), execution does not halt. The subsequent lines still execute:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [2](#0-1) 

This drains `usdcE` from the `directDepositV1` vault and sends it to `msg.sender` with no USDC payment received.

Contrast this with the rest of the codebase, which consistently uses `ERC20Helper.safeTransferFrom` that wraps the call and requires `success && (data.length == 0 || abi.decode(data, (bool)))`:

```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [3](#0-2) 

`replaceUsdcEWithUsdc` is the only production function that bypasses this pattern.

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc` with a `subaccount` whose `directDepositV1` holds a non-zero `usdcE` balance, while having zero USDC allowance or balance, will:

- Pay nothing (the `transferFrom` silently returns `false`).
- Receive the full `usdcE` balance of that `directDepositV1` vault.

This is a direct asset theft: `usdcE` collateral belonging to the subaccount owner is drained to the attacker with no compensation. The corrupted state delta is: `directDepositV1.usdcE balance → 0`, `attacker.usdcE balance += balance`, `directDepositV1.usdc balance unchanged (still 0)`.

---

### Likelihood Explanation

The function is `external` with no access control beyond `block.chainid == 57073` (Ink mainnet). [4](#0-3)  Any unprivileged caller on Ink can invoke it. The only precondition is that a target `directDepositV1` holds a non-zero `usdcE` balance, which is the exact scenario the function is designed to handle. The attacker simply needs to call it without having approved USDC.

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with every other transfer in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` in `ERC20Helper` already handles both reverting and non-reverting ERC-20 tokens. [5](#0-4) 

---

### Proof of Concept

1. Identify any `subaccount` on Ink (chain 57073) whose `directDepositV1Address[subaccount]` is non-zero and holds `usdcE` balance > 0.
2. As an attacker with **zero USDC allowance** to `ContractOwner`, call:
   ```solidity
   ContractOwner.replaceUsdcEWithUsdc(subaccount)
   ```
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, balance)` returns `false` (no allowance). Return value is discarded. [1](#0-0) 
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes, pulling all `usdcE` into `ContractOwner`. [6](#0-5) 
5. `IERC20Base(usdcE).safeTransfer(attacker, balance)` executes, sending all `usdcE` to the attacker. [7](#0-6) 
6. Attacker receives `usdcE` collateral for free. The subaccount owner's deposited collateral is permanently lost.

### Citations

**File:** core/contracts/ContractOwner.sol (L608-610)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
```

**File:** core/contracts/ContractOwner.sol (L616-616)
```text
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

**File:** core/contracts/ContractOwner.sol (L617-619)
```text
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
