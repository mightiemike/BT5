### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `sender` = router address, not the end user. If the pool admin allowlists the router — a natural action to enable router-mediated swaps — every unprivileged user can bypass the allowlist entirely by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(); the router when routed
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

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), and `sender` = the router when the user goes through `MetricOmmSimpleRouter`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no identity forwarding:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The end user's address is never surfaced to the extension.

**Contrast with `DepositAllowlistExtension`**: for deposits the pool passes `owner` (the position owner, i.e. the economically relevant party) as a distinct argument, and the extension correctly gates on `owner`. For swaps no equivalent "end-user" argument exists; only `sender` (the immediate caller) is available. The two allowlist extensions therefore apply fundamentally different identity semantics, creating an exploitable asymmetry.

---

### Impact Explanation

If the pool admin allowlists the router (`setAllowedToSwap(pool, router, true)`) to enable router-mediated swaps — a natural and expected administrative action — then `allowedSwapper[pool][router] = true`. Every call through `MetricOmmSimpleRouter` passes the check regardless of who the end user is. Any unprivileged address can drain liquidity from a pool that was intended to be restricted to a specific set of counterparties. The allowlist provides zero protection once the router is on it.

Conversely, if the router is *not* allowlisted, legitimately allowlisted end users cannot swap through the router at all, breaking the expected swap flow for those users.

---

### Likelihood Explanation

Medium. A pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants users to be able to use the standard router will naturally add the router to the allowlist. The extension is documented as "Gates `swap` by swapper address, per pool" — nothing in the interface or NatDoc warns that allowlisting the router is equivalent to disabling the allowlist for all router users. The `DepositAllowlistExtension` correctly gates the end user (`owner`), so a pool admin familiar with the deposit side will reasonably expect the swap side to behave analogously.

---

### Recommendation

Two complementary fixes:

1. **Short-term (documentation):** Add explicit NatDoc to `SwapAllowlistExtension` and `setAllowedToSwap` warning that `sender` is the immediate caller of `pool.swap()`, and that allowlisting any public router is equivalent to opening the pool to all users of that router.

2. **Long-term (code):** Allow the extension to read the true end-user identity from `extensionData`. The router already forwards `params.extensionData` to the pool; the pool forwards it to the extension. A convention where the router ABI-encodes `msg.sender` into `extensionData` (or a dedicated field) would let the extension gate on the real originator. Alternatively, introduce a dedicated `swapOriginator` field in the `beforeSwap` signature analogous to `owner` in `beforeAddLiquidity`.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists only alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin also allowlists the router to enable router UX:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — msg.sender in pool = router.
6. Pool calls extension.beforeSwap(router, bob, ...) — sender = router.
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes; the allowlist is fully bypassed.
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
