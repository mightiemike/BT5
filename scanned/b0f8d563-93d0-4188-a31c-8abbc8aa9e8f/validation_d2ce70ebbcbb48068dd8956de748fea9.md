The vulnerability claim is valid. Here is the full analysis:

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` parameter (the actual caller of `addLiquidity`) and instead gates access on `owner` (the position beneficiary). Because `pool.addLiquidity` accepts an arbitrary `owner` address from any caller, any unprivileged address can bypass the allowlist by specifying an allowlisted address as `owner`.

---

### Finding Description

The pool's `addLiquidity` function passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to the extension hook: [1](#0-0) 

The extension interface explicitly exposes both parameters: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` entirely (unnamed first parameter) and only checks `owner`: [3](#0-2) 

The check `allowedDepositor[msg.sender][owner]` asks "is the position beneficiary allowlisted?" — not "is the actual depositor allowlisted?". Since `pool.addLiquidity` places no restriction on who may supply an arbitrary `owner` value, any address B can call `pool.addLiquidity(owner=A, ...)` where A is allowlisted, and the extension will pass.

The router (`MetricOmmPoolLiquidityAdder`) explicitly supports and tests this "deposit on behalf of another owner" pattern — it validates only that `owner != address(0)`, not that `msg.sender == owner`: [4](#0-3) 

The existing test suite even confirms this works without any allowlist in place: [5](#0-4) 

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for restricting who may add liquidity to a pool. Its invariant — "only approved addresses can deposit" — is completely defeated. Any unprivileged address can inject liquidity into a restricted pool at any time by naming an allowlisted address as `owner`. The attacker spends their own tokens, but the liquidity is credited to the allowlisted address's position. This enables:

- **Allowlist bypass**: The pool admin's access control is rendered meaningless.
- **Griefing**: An attacker can force liquidity into an allowlisted LP's position at an unfavorable price or bin composition, diluting or distorting that LP's position without consent.
- **Broken core functionality**: The extension's stated purpose ("Gates `addLiquidity` by depositor address, per pool") is entirely non-functional.

---

### Likelihood Explanation

The attack path is trivially reachable: call `pool.addLiquidity(owner=<any_allowlisted_address>, ...)` directly, or use the router's `addLiquidityExactShares(pool, owner=<allowlisted>, ...)`. No special privileges, no malicious setup, and no non-standard token behavior is required. The only precondition is knowing one allowlisted address, which is readable from `allowedDepositor` (a public mapping). [6](#0-5) 

---

### Recommendation

Replace the ignored first parameter with `sender` and check it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to allow deposits on behalf of an allowlisted owner (i.e., a third party pays for an allowlisted LP), then both `sender` and `owner` should be checked, and the semantics should be documented explicitly.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with DepositAllowlistExtension, only `alice` is allowlisted.
// Attacker is `bob` (not allowlisted).

function test_bypassAllowlistViaOwner() public {
    // Only alice is allowlisted
    depositExtension.setAllowedToDeposit(address(pool), alice, true);

    // Bob is NOT allowlisted — but specifies alice as owner
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    vm.prank(bob);
    // This should revert but does NOT — bob bypasses the allowlist
    helper.addLiquidityExactShares(
        address(pool), alice, 1, d, type(uint256).max, type(uint256).max, ""
    );

    // Liquidity was added to alice's position using bob's tokens
    uint256 aliceShares = stateView.positionBinShares(address(pool), alice, 1, int8(4));
    assertGt(aliceShares, 0); // passes — allowlist bypassed
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```
