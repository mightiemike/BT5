### Title
Unchecked `transferFrom()` Return Value Enables Silent Failure and USDC.e Drain in `replaceUsdcEWithUsdc()` - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(...)` without checking its return value. The protocol's own `ERC20Helper.safeTransferFrom` wrapper is imported and available via `using ERC20Helper for IERC20Base`, but is not used here. If the USDC token at the hardcoded address returns `false` on failure rather than reverting, the `transferFrom` silently fails while the subsequent USDC.e withdrawal and transfer to the caller still execute, draining USDC.e from the `DirectDepositV1` contract without the caller providing any USDC.

---

### Finding Description

`replaceUsdcEWithUsdc()` is an externally callable function (no access modifier) restricted only to chain ID 57073 (Ink). Its intended logic is a 1:1 swap: pull USDC from `msg.sender` into the DDA, then withdraw USDC.e from the DDA back to `msg.sender`.

The critical sequence is:

```solidity
// ContractOwner.sol lines 614–619
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← raw, unchecked
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));       // ← always executes
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                 // ← always executes
``` [1](#0-0) 

The `transferFrom` return value is never inspected. If it returns `false` (as non-reverting ERC-20 tokens such as USDT-style tokens do), execution continues unconditionally into `withdraw()` and `safeTransfer()`.

By contrast, the immediately following line uses `safeTransfer` correctly, and the `ERC20Helper` library — which implements the safe wrapper pattern — is already imported and bound to `IERC20Base` via `using ERC20Helper for IERC20Base` at line 24. [2](#0-1) 

The safe wrapper `ERC20Helper.safeTransferFrom` uses a low-level `call` and explicitly requires `success && (data.length == 0 || abi.decode(data, (bool)))`: [3](#0-2) 

This pattern is used correctly everywhere else in the codebase (e.g., `BaseWithdrawPool.safeTransferFrom` at line 197, `Clearinghouse.handleWithdrawTransfer` at line 383), but is absent from this specific call site. [4](#0-3) 

---

### Impact Explanation

If `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` silently returns `false`:

1. No USDC is transferred from the attacker to the DDA.
2. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` still executes, moving all USDC.e from the DDA to `ContractOwner`.
3. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` still executes, sending all USDC.e to the attacker.

The attacker receives the full USDC.e balance of the DDA without providing any USDC. This is a direct, complete asset drain of the `DirectDepositV1` contract's USDC.e holdings. The corrupted asset delta is: attacker gains `balance` USDC.e, DDA loses `balance` USDC.e, no USDC is deposited.

---

### Likelihood Explanation

The function is callable by any unprivileged external address on chain 57073 with no further preconditions beyond a non-zero DDA existing for some subaccount. The USDC token is hardcoded at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink chain — if this deployment follows a non-reverting ERC-20 pattern (returning `false` on failure rather than reverting), the exploit is directly triggerable. Even if the current USDC deployment reverts, the code is structurally incorrect: a token upgrade, migration, or future listing of a non-reverting token at this address would immediately expose the drain path. The inconsistency with the adjacent `safeTransfer` call on line 618 confirms this is an oversight rather than an intentional design choice.

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's own `safeTransferFrom` wrapper, which is already available via `using ERC20Helper for IERC20Base`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

---

### Proof of Concept

1. A `DirectDepositV1` DDA exists for some `subaccount` and holds `N` USDC.e.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on chain 57073 with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (no revert) due to zero allowance, under a non-reverting USDC implementation.
4. Execution continues: `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` USDC.e to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` transfers `N` USDC.e to the attacker.
6. Attacker has drained `N` USDC.e from the DDA, providing zero USDC in return. [1](#0-0)

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

**File:** core/contracts/BaseWithdrawPool.sol (L192-198)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```
