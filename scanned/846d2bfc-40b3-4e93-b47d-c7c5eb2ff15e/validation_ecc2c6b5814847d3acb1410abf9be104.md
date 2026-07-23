### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `sender = router`, not the actual user. If the pool admin allowlists the router address (the natural step to enable router-mediated swaps for their users), every unprivileged user can bypass the per-user allowlist entirely by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The `sender` argument is forwarded from `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's `swap()` call:

```solidity
// ExtensionCalling.sol line 149-177
function _beforeSwap(...) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))  // sender = msg.sender of pool.swap()
    );
}
``` [2](#0-1) 

And in `MetricOmmPool.swap()`, `msg.sender` is passed as `sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- the immediate caller, not the end user
    recipient,
    ...
);
``` [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is stored only in the router's transient callback context and is never surfaced to the extension.

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, institutional partners) must also allowlist the router address if they want their approved users to be able to use the router. Once the router is allowlisted, the per-user gate collapses: **any** unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and the extension will pass because it sees `sender = router`. The LP's funds are exposed to unauthorized counterparties, violating the access-control invariant the pool admin configured and potentially causing unexpected LP losses from swap patterns the LP did not price for.

### Likelihood Explanation

The likelihood is medium. The `SwapAllowlistExtension` is a production extension explicitly documented as gating "swap by swapper address, per pool." A pool admin who wants their allowlisted users to use the router will naturally allowlist the router address, unaware that doing so opens the gate to all users. The router is a public, permissionless contract, so any attacker can exploit this immediately after the router is allowlisted.

### Recommendation

The extension should check the **ultimate user** rather than the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is fragile.

2. **Preferred — check `sender` (the pool's `msg.sender`) only when it is not a known router, and require routers to forward the real user**: Add a registry of trusted routers to `BaseMetricExtension`; when `sender` is a trusted router, decode the real user from `extensionData` and check that address instead.

3. **Simplest fix**: Document that allowlisting the router disables per-user gating, and provide a separate `RouterSwapAllowlistExtension` that reads the real user from a standardized `extensionData` field populated by the router.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        recipient: bob,
        ...
    })
  - Router calls pool.swap(bob, true, X, ...)
  - Pool calls _beforeSwap(msg.sender=router, bob, ...)
  - Extension checks allowedSwapper[pool][router] → true → passes
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - bob bypasses the SwapAllowlistExtension
  - The pool's access-control invariant is broken
  - LP funds are exposed to unauthorized swap counterparties
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
