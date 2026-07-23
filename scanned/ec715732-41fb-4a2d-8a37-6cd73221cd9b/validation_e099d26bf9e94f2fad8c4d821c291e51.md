### Title
`SwapAllowlistExtension` Gates Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the allowlist checks the router's address rather than the end user's address. If the router is allowlisted (a natural admin action to permit router-mediated swaps), every unprivileged user can bypass the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the original `pool.swap()` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

So the extension receives `sender = router address`, not the end user's address. The allowlist check becomes `allowedSwapper[pool][router]` — if the router is allowlisted, every user who calls through the router bypasses the per-user gate.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position recipient), which is correctly preserved through the liquidity adder path. The swap path has no equivalent end-user identity forwarding.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` in restrictive mode (e.g., a private institutional pool, a KYC-gated pool, or a pool restricted to specific counterparties) relies on the allowlist as its primary access control for swaps. If the pool admin allowlists the router — a natural action to permit allowlisted users to swap via the standard periphery — the allowlist is rendered ineffective: any unprivileged user can call `router.exactInputSingle()` or `router.exactInput()` and the hook will pass because it sees the router address, not the user. Unauthorized users can drain LP value through swaps the pool was designed to prohibit.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the `MetricOmmSimpleRouter`. This is a natural, non-malicious configuration: a pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. Once that configuration is in place, any unprivileged user can exploit it with a single public call to the router. No special privileges, flash loans, or unusual tokens are required.

---

### Recommendation

The `beforeSwap` hook should gate the economically relevant actor — the end user — not the immediate caller of `pool.swap()`. Two options:

1. **Check `sender` only for direct swaps; require the router to forward the originating user.** The router could pass the original `msg.sender` in `extensionData`, and the extension could decode and check it. This requires a coordinated change to the router and extension.

2. **Remove the router from the allowlist and require allowlisted users to call `pool.swap()` directly.** This is the simplest fix but breaks router usability for restricted pools.

The cleanest production fix is option 1: define a convention where the router encodes the originating user in `extensionData`, and the `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - allowAllSwappers[pool] = false
  - allowedSwapper[pool][alice] = true        // alice is the only allowed swapper
  - allowedSwapper[pool][router] = true       // admin allowlists router so alice can use it

Attack:
  1. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: pool,
           recipient: bob,
           zeroForOne: true,
           amountIn: X,
           ...
       }))
  2. Router calls pool.swap() with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true → passes
  5. Bob's swap executes against the restricted pool
  6. Bob extracts value the pool admin intended to reserve for alice only
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
