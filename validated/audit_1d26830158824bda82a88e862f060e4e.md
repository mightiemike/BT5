### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain Without USDC Payment - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the boolean return value. The function is publicly callable (no access control beyond a chain-id check). If the `transferFrom` returns `false` instead of reverting, execution continues: the DDA's usdcE is withdrawn to `ContractOwner` and forwarded to `msg.sender` — with no USDC ever received in exchange.

---

### Finding Description

`ContractOwner` imports and uses `ERC20Helper for IERC20Base`, which provides a `safeTransferFrom` wrapper that checks the return value and reverts on failure. [1](#0-0) 

However, `replaceUsdcEWithUsdc` bypasses this safe wrapper and calls raw `.transferFrom()` directly: [2](#0-1) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`: [3](#0-2) 

The return value is silently discarded. The function's three-step logic is:

1. Pull `balance` USDC from `msg.sender` into `directDepositV1` — **unchecked**
2. Withdraw `usdcE` from `directDepositV1` to `ContractOwner` — always executes
3. `safeTransfer` usdcE to `msg.sender` — always executes

If step 1 fails silently, steps 2 and 3 still execute, transferring usdcE to the caller for free.

The `ERC20Helper.safeTransferFrom` used everywhere else in the codebase correctly checks the return value: [4](#0-3) 

---

### Impact Explanation

An attacker can drain all usdcE held in any `DirectDepositV1` contract associated with any subaccount on chain 57073 (Ink), without providing any USDC. The stolen asset is usdcE, a bridged stablecoin. The DDA subaccount loses its usdcE collateral balance permanently, corrupting the collateral state of the affected subaccount.

---

### Likelihood Explanation

The function has no `onlyOwner` or similar access control — only `require(block.chainid == 57073, ERR_UNAUTHORIZED)`. Any unprivileged caller on the Ink chain can invoke it for any subaccount that has a registered DDA with a nonzero usdcE balance. The trigger requires only knowing a valid `subaccount` bytes32 value with a DDA holding usdcE, which is observable on-chain. Likelihood is **high** given zero permission requirements and direct financial gain.

---

### Recommendation

Replace the raw `.transferFrom()` call with the `safeTransferFrom` method already available via the `ERC20Helper` library that is imported and used in scope:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [5](#0-4) 

---

### Proof of Concept

1. A subaccount `S` has a registered `DirectDepositV1` DDA holding 1000 usdcE.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(S)` on chain 57073 with **zero USDC allowance**.
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` (insufficient allowance) — return value is not checked, execution continues.
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers 1000 usdcE from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` sends 1000 usdcE to the attacker.
6. Attacker receives 1000 usdcE having paid 0 USDC. The DDA is drained and the subaccount's collateral is destroyed. [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L23-24)
```text
    using MathSD21x18 for int128;
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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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
