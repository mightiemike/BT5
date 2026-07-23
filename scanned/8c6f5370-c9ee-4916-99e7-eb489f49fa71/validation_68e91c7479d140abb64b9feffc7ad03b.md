### Title
`SwapAllowlistExtension` gates the router's address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router's address rather than the actual swapper, making the per-user allowlist trivially bypassable.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool calls `_beforeSwap(msg.sender /*= sender*/, recipient, ...)` and forwards that value into the extension call:

```solidity
// ExtensionCalling.sol
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInput` (or `exactInputSingle` / `exactOutput*`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol – exactInput
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne, amount, ..., params.extensionDatas[i]
    );
``` [3](#0-2) 

So `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][actual_user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` parameter (the actual position owner), not `sender`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The asymmetry is the root cause: the deposit guard correctly uses the economically relevant identity (`owner`), while the swap guard uses the wrong identity (`sender` = router).

---

### Impact Explanation

Two concrete fund-impacting scenarios arise:

**Scenario A – Allowlist bypass (primary):** A pool admin allowlists the router address (a natural configuration when they want to permit "all router-mediated swaps" while blocking direct pool calls). Any user, including those the admin intended to block, can now swap freely through the router. The per-user allowlist is completely defeated.

**Scenario B – Broken allowlist for legitimate users:** A pool admin allowlists specific user addresses but does not allowlist the router. Every allowlisted user who routes through `MetricOmmSimpleRouter` is blocked (`NotAllowedToSwap`), making the pool's swap path unusable for the intended participants. This breaks core pool swap functionality.

Both scenarios satisfy the allowed impact gate: Scenario A is an admin-boundary break where an unprivileged path bypasses a configured guard; Scenario B is broken core pool functionality causing an unusable swap flow.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will be affected. The trigger requires no special privilege — any public user calling `exactInputSingle` or `exactInput` through the router reaches the vulnerable path.

---

### Recommendation

Pass the actual end-user identity to the extension. The pool should forward the original `msg.sender` of the router call, not the router's address. One approach: the router sets a transient-storage slot with the real user address before calling `pool.swap`, and the pool reads it as `sender` for extension dispatch. Alternatively, mirror the deposit pattern and add a dedicated `swapper` parameter (distinct from `sender`) that the pool populates from a trusted callback context, so the extension always sees the economically relevant actor.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
// 2. Pool admin allowlists the router address:
//    swapExtension.setAllowedToSwap(pool, address(router), true);
//    (Admin intends to allow router-mediated swaps for all users.)
// 3. Pool admin does NOT allowlist blockedUser.

// Attack:
// blockedUser calls router.exactInputSingle(...) targeting the pool.
// Inside exactInput, the router calls pool.swap(recipient, ...).
// pool.msg.sender == address(router).
// _beforeSwap passes sender = address(router) to SwapAllowlistExtension.
// allowedSwapper[pool][router] == true  →  check passes.
// blockedUser's swap executes successfully despite not being allowlisted.

function test_swapAllowlistBypassViaRouter() public {
    address blockedUser = makeAddr("blockedUser");

    // Admin allowlists the router (not the user)
    vm.prank(admin);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // blockedUser swaps through the router — should revert but does not
    vm.prank(blockedUser);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: blockedUser,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: false,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    // blockedUser successfully swapped — allowlist bypassed
}
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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
