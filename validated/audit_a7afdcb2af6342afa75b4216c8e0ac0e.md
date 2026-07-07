### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Draining usdcE from DDAs Without Providing USDC - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` at line 616 without checking its boolean return value. The same function correctly uses `safeTransfer` for the outgoing usdcE leg (line 618), but the incoming USDC leg is unguarded. If the USDC token on chain 57073 (Ink) returns `false` on a failed transfer instead of reverting, the function proceeds to drain usdcE from the target DDA and deliver it to the caller — with no USDC received in exchange.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public, permissionless function (no `onlyOwner` or `onlyDeployer` modifier) that is intended to swap bridged USDC.e held in a `DirectDepositV1` address for native USDC. The swap logic is:

1. Read the usdcE balance of the DDA.
2. Pull `balance` USDC from `msg.sender` into the DDA.
3. Withdraw usdcE from the DDA to `ContractOwner`.
4. Forward usdcE to `msg.sender`.

Step 2 uses a raw `transferFrom` call whose `bool` return value is silently discarded:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);  // line 616 — return value ignored
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));         // line 617
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                   // line 618 — safe
``` [1](#0-0) 

The contract already imports and applies `ERC20Helper` (which provides a `safeTransferFrom` that checks the return value and reverts on failure) via `using ERC20Helper for IERC20Base`: [2](#0-1) 

`ERC20Helper.safeTransferFrom` performs a low-level call and requires `success && (data.length == 0 || abi.decode(data, (bool)))`: [3](#0-2) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`: [4](#0-3) 

Because the return value is never read, a USDC implementation that returns `false` on failure (e.g., insufficient allowance, insufficient balance) instead of reverting will allow execution to continue past line 616 into lines 617–618, completing the usdcE withdrawal and delivery to the caller.

---

### Impact Explanation

An attacker with zero USDC balance or zero USDC allowance can call `replaceUsdcEWithUsdc` targeting any subaccount whose DDA holds usdcE. If the USDC token on chain 57073 returns `false` on a failed `transferFrom` rather than reverting:

- The DDA loses its entire usdcE balance.
- The attacker receives that usdcE for free.
- The subaccount owner's DDA is permanently drained of usdcE, which was intended to be credited as collateral.

The corrupted asset delta is: `usdcE balance of directDepositV1[subaccount]` transferred to attacker at zero cost. This is a direct, permanent loss of user funds.

---

### Likelihood Explanation

- The function is externally callable by any address on chain 57073 with no access control.
- The only precondition is that a target DDA (`directDepositV1Address[subaccount]`) exists and holds a non-zero usdcE balance.
- Whether the native USDC at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink (chain 57073) reverts or returns `false` on failure determines exploitability. Non-reverting ERC20 behavior is a known class of deployed tokens (e.g., ZRX, early USDT). If the Ink USDC deployment follows this pattern, exploitation is trivial and requires no special privileges.
- Even if the current USDC deployment reverts, the missing check is a latent defect: a future token upgrade or a different collateral token added to the same pattern would re-expose the issue.

---

### Recommendation

Replace the raw `transferFrom` call with the already-available `safeTransferFrom` from `ERC20Helper`:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` is already in scope via `using ERC20Helper for IERC20Base` and will revert if the transfer returns `false` or fails at the call level. [3](#0-2) 

---

### Proof of Concept

1. A subaccount DDA at `directDepositV1Address[victim_subaccount]` holds 1000 usdcE.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(victim_subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` (no revert) — execution continues.
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers 1000 usdcE from DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` delivers 1000 usdcE to the attacker.
6. Attacker holds 1000 usdcE; DDA holds 0 usdcE; no USDC was ever transferred. [5](#0-4)

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
```
