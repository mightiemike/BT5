### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` delivered to the extension is the router address — not the actual EOA. If the pool admin allowlists the router (the only way to make router-mediated swaps work at all), every user on the internet can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, so `sender` delivered to the extension is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an inescapable dilemma for any pool that deploys `SwapAllowlistExtension`:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user bypasses the per-user allowlist by routing through the public router |
| Router **is not** allowlisted | No user can swap through the router; allowlisted users must call the pool directly |

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner, passed explicitly by the caller), not `sender`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or a private OTC desk) loses that restriction entirely the moment the router is allowlisted. Any anonymous EOA can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool, draining LP value at oracle-anchored prices with no access control. This is a direct loss of the access-control invariant the extension is designed to enforce, with fund-impacting consequences for LPs who deposited under the assumption that only approved counterparties could trade.

---

### Likelihood Explanation

The router is a public, production periphery contract. Any pool that wants to support standard user flows (slippage protection, multi-hop, deadline checks) must allowlist the router. The moment it does, the bypass is unconditional and requires no special privileges or setup from the attacker — a single call to `exactInputSingle` suffices.

---

### Recommendation

Pass the **original initiating user** through the swap call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Add an `originator` field to `extensionData`**: the router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires no core changes.
2. **Mirror the deposit pattern**: add an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) that the router fills with `msg.sender`, and pass that to `_beforeSwap` instead of the pool's `msg.sender`.

Option 2 is cleaner and consistent with how `DepositAllowlistExtension` correctly gates the position owner rather than the immediate caller.

---

### Proof of Concept

```
1. Pool is deployed with SwapAllowlistExtension.
2. Admin allowlists only address(0xALICE) as an approved swapper.
3. Admin also allowlists address(router) so that router-mediated swaps work.
4. address(0xEVE) — not on the allowlist — calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
5. Pool calls _beforeSwap(sender = router, ...).
6. Extension checks allowedSwapper[pool][router] → true.
7. Swap executes. EVE receives output tokens. Allowlist is bypassed.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at: [6](#0-5) 

where `sender` is the router address, not the actual trading counterparty.

### Citations

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
