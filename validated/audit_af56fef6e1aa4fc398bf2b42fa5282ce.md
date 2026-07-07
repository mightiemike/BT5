### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Allows USDC.e Drain Without Providing USDC — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking its boolean return value. If the call returns `false` instead of reverting, execution continues, withdrawing USDC.e from the target DDA and sending it to the caller — who provided no USDC. The function is callable by any unprivileged address on Ink Chain (chain ID 57073).

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no `onlyOwner` or similar access control — only a chain ID check. Its purpose is to let a caller swap USDC.e held in a `DirectDepositV1` (DDA) for USDC: the caller sends USDC in, and receives USDC.e out.

The critical transfer at line 616 uses a raw `IERC20Base.transferFrom` call whose `bool` return value is silently discarded:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // return value ignored
``` [1](#0-0) 

If this call returns `false` (i.e., the transfer fails without reverting), execution continues to:

1. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulls all USDC.e from the DDA into `ContractOwner`.
2. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends that USDC.e to the caller.

The caller receives USDC.e without having transferred any USDC.

This is structurally inconsistent with the rest of the codebase. The `ERC20Helper` library exists precisely to handle this case:

```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [2](#0-1) 

And line 618 in the same function correctly uses `safeTransfer`, making the omission on line 616 a clear inconsistency: [3](#0-2) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`, confirming the return value is available but unused: [4](#0-3) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink Chain returns `false` on a failed `transferFrom` (e.g., insufficient allowance) rather than reverting, any caller can:

- Drain USDC.e from any DDA that holds a balance, receiving it for free.
- The DDA loses its entire USDC.e balance; the attacker gains it without providing USDC.

The corrupted asset delta is: DDA USDC.e balance → 0; attacker USDC.e balance → +`balance`.

---

### Likelihood Explanation

The function is `external` with no access control beyond `block.chainid == 57073`. Any unprivileged user on Ink Chain can call it for any registered `subaccount`. The exploitability depends on whether the specific USDC token at the hardcoded address returns `false` on failure rather than reverting. Standard Circle USDC reverts on insufficient allowance, which would prevent exploitation in the common case. However, the structural bug is present and the inconsistency with the rest of the codebase (which uniformly uses `ERC20Helper.safeTransferFrom`) is concrete. If the token's behavior changes (e.g., via upgrade) or a different token is used at that address, the vulnerability becomes immediately exploitable.

---

### Recommendation

Replace the raw `transferFrom` call with the `ERC20Helper.safeTransferFrom` wrapper already used throughout the codebase:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [5](#0-4) 

---

### Proof of Concept

1. Identify any `subaccount` whose DDA (`directDepositV1Address[subaccount]`) holds a non-zero USDC.e balance.
2. Call `replaceUsdcEWithUsdc(subaccount)` from an EOA with zero USDC balance and zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` returns `false` (no revert).
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` executes — USDC.e moves from DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` executes — USDC.e moves from `ContractOwner` to attacker.
6. Attacker holds `balance` USDC.e; DDA holds 0 USDC.e; no USDC was ever transferred. [6](#0-5)

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

**File:** core/contracts/libraries/ERC20Helper.sol (L14-20)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
```
