### Title
Unchecked Return Value on Raw `transferFrom` in `replaceUsdcEWithUsdc` Enables usdcE Theft Without Providing USDC — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` directly without checking its return value. If the USDC token at the hardcoded address on chain 57073 (Ink) returns `false` on failure rather than reverting, an attacker can call this function with zero USDC allowance, silently skip the inbound USDC transfer, and still receive the full usdcE balance from the target `DirectDepositV1` contract.

---

### Finding Description

`replaceUsdcEWithUsdc` is an unguarded external function (only restricted by `block.chainid == 57073`) that is intended to atomically swap usdcE held in a `DirectDepositV1` (DDA) contract for USDC provided by the caller:

```
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);  // line 616 — raw call, return value discarded
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                  // line 618 — uses ERC20Helper.safeTransfer
``` [1](#0-0) 

The outbound leg (line 618) correctly uses `ERC20Helper.safeTransfer`, which low-level-calls `transfer` and asserts `success && (data.length == 0 || abi.decode(data, bool))`. [2](#0-1) 

`ERC20Helper` also exposes `safeTransferFrom` with the same return-value check: [3](#0-2) 

The inbound leg (line 616) bypasses this wrapper entirely and calls `transferFrom` directly on the `IERC20Base` interface, whose return value is declared as `bool` but is never inspected. [4](#0-3) 

`ContractOwner` imports and `using`-attaches `ERC20Helper` to `IERC20Base`, so `safeTransferFrom` was available and should have been used. [5](#0-4) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink (chain 57073) returns `false` on a failed `transferFrom` (e.g., insufficient allowance) instead of reverting:

1. The inbound USDC transfer silently fails — no USDC moves from the attacker to the DDA.
2. `DirectDepositV1.withdraw` still executes, pulling the entire usdcE balance from the DDA to `ContractOwner`.
3. `safeTransfer` sends that usdcE balance to the attacker.

The attacker receives usdcE tokens belonging to protocol users without providing any USDC. Every DDA on chain 57073 that holds a non-zero usdcE balance is drainable in a single call. [6](#0-5) 

---

### Likelihood Explanation

The function is callable by any unprivileged external address on chain 57073 — no role, no approval, no prior state required beyond a DDA with a non-zero usdcE balance. The exploitability depends on whether the specific USDC deployment at the hardcoded address returns `false` or reverts on failure. Many non-Circle or bridged USDC variants (including wrapped/bridged stablecoins common on newer L2s) follow the older ERC20 pattern of returning `false`. The inconsistency — `safeTransfer` used on the outbound leg but raw `transferFrom` on the inbound leg — confirms this is an unintentional oversight, not a deliberate design choice.

---

### Recommendation

Replace the raw `transferFrom` call on line 616 with `ERC20Helper.safeTransferFrom`, consistent with how the rest of the codebase handles ERC20 transfers:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [7](#0-6) 

---

### Proof of Concept

**Setup:** A DDA for `subaccount` exists on chain 57073 and holds `N` usdcE tokens.

**Attacker steps:**
1. Do **not** approve any USDC to `ContractOwner`.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)`.
3. Line 616 executes `IERC20Base(usdc).transferFrom(attacker, dda, N)` — if USDC returns `false`, execution continues without revert.
4. Line 617 executes `DirectDepositV1(dda).withdraw(usdcE)` — transfers `N` usdcE from the DDA to `ContractOwner`.
5. Line 618 executes `IERC20Base(usdcE).safeTransfer(attacker, N)` — sends `N` usdcE to the attacker.

**Result:** Attacker receives `N` usdcE; no USDC was ever transferred. The DDA's usdcE balance is zero. The user whose subaccount the DDA belongs to loses their usdcE collateral. [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
```
