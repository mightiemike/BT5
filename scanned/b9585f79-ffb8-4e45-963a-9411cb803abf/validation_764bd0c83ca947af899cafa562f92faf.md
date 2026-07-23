### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Per-Pool Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (a necessary step for any router-mediated swap to work on that pool), every unprivileged user can bypass the per-user allowlist by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

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
```

`msg.sender` here is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by the pool. The pool always sets this to its own `msg.sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so the pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The router call path:

```solidity
// MetricOmmSimpleRouter.sol line 71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← user-supplied, extension ignores it
    );
```

The router does not inject the originating user's address into `extensionData`, and the extension does not read `extensionData` at all. The extension has no way to recover the actual end user.

**Bypass sequence:**

1. Admin deploys pool with `SwapAllowlistExtension` in the `beforeSwap` order.
2. Admin allowlists specific users: `setAllowedToSwap(pool, user1, true)`.
3. Admin also allowlists the router so that `user1` can use it: `setAllowedToSwap(pool, router, true)`.
4. `user2` (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap()`; pool passes `sender = router` to the extension.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `user2` successfully swaps on a pool they were never authorized to access.

The admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users from using the same router, because the router is a single address. Allowlisting the router is an all-or-nothing decision.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly uses the `owner` parameter (the position owner, explicitly passed by the pool), not `sender` (the direct caller). This asymmetry means the deposit guard works correctly through the `MetricOmmPoolLiquidityAdder`, while the swap guard does not work correctly through the router.

---

### Impact Explanation

Any unprivileged user can trade on a curated, allowlist-gated pool by routing through the public `MetricOmmSimpleRouter`. The pool admin's access-control intent is defeated. Pools designed for restricted participant sets (e.g., institutional, KYC-gated, or partner-only pools) are fully open to any caller who uses the router. Unauthorized swaps execute at the live oracle price and directly affect LP balances in the touched bins.

---

### Likelihood Explanation

The bypass requires the admin to have allowlisted the router address. This is a natural and expected configuration step: without it, no allowlisted user can use the router either, making the router useless for that pool. Any user who discovers the allowlist is active can immediately attempt the bypass by calling the router. No special privileges, flash loans, or multi-transaction setup are required.

---

### Recommendation

The extension should identify the actual end user rather than the direct caller of `pool.swap()`. Two approaches:

1. **Decode the user from `extensionData`**: require the router (and any other intermediary) to ABI-encode the originating `msg.sender` into `extensionData`, and verify it in the extension. This requires a coordinated change to the router.

2. **Check `recipient` instead of `sender`**: for swap allowlists the economically relevant actor is often the recipient of the output tokens. Switching the check to `recipient` would correctly gate the beneficiary regardless of which contract calls `pool.swap()`. This is a simpler fix but changes the semantics of the allowlist.

Either way, the extension must not rely on `sender` alone when the pool is expected to be reachable through public router contracts.

---

### Proof of Concept

```
Pool admin:
  setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attacker (bob, not allowlisted):
  router.exactInputSingle({
      pool:        pool,
      recipient:   bob,
      zeroForOne:  true,
      amountIn:    1_000e6,
      ...
  })
  // pool.swap() is called with msg.sender = router
  // extension checks allowedSwapper[pool][router] → true
  // swap executes; bob receives output tokens
  // NotAllowedToSwap is never raised
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
