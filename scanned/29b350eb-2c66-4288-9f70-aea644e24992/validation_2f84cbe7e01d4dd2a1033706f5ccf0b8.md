### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the original user. If the pool admin allowlists the router to enable router-mediated swaps — a routine operational decision — every user, including those absent from the per-user allowlist, bypasses the guard entirely.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` parameter forwarded to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks `sender` against the per-pool allowlist.** [3](#0-2) 

`msg.sender` inside the extension is the pool (the caller of the extension), and `sender` is whoever called `pool.swap()`.

**Step 3 — MetricOmmSimpleRouter is the direct caller of `pool.swap()`.**

Every `exact*` entry point in the router calls `pool.swap()` directly: [4](#0-3) 

At the pool level, `msg.sender = address(router)`. Therefore the extension evaluates `allowedSwapper[pool][address(router)]`, not `allowedSwapper[pool][original_user]`.

**Resulting failure modes:**

| Pool admin action | Consequence |
|---|---|
| Allowlists the router (to enable router-mediated swaps) | **Every user** can bypass the per-user allowlist by routing through the router |
| Does not allowlist the router | **Every allowlisted user** is blocked from using the router — broken core swap flow |

Neither option lets the pool admin achieve the intended policy: "only allowlisted users may swap, whether directly or through the router."

Note: `DepositAllowlistExtension` does **not** share this flaw — it checks `owner` (the position owner passed explicitly), not `sender`, so the liquidity adder path is correctly gated. [5](#0-4) 

---

### Impact Explanation

When the router is allowlisted (the only way to enable router-mediated swaps), any unprivileged user can trade on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The `SwapAllowlistExtension` guard — the sole mechanism protecting curated pools from unauthorized counterparties — is silently bypassed. LPs on institutional or KYC-gated pools are exposed to trades from actors the pool admin explicitly excluded.

---

### Likelihood Explanation

High. The router is the primary user-facing swap entry point in the periphery. Pool admins who deploy pools with `SwapAllowlistExtension` and want to support standard UX will allowlist the router. The bypass is then unconditionally available to any address. No exploit setup beyond a normal router call is required.

---

### Recommendation

The extension must gate on the economically relevant actor, not the proximate caller. Concrete options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to both the router and the extension.
2. **Dedicated router wrapper**: The router calls a pool-side helper that records the original caller in transient storage before invoking `swap`, and the extension reads that slot.
3. **Document incompatibility**: If neither fix is adopted, document explicitly that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that pool admins must never allowlist the router address.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router to support router-mediated swaps:
       swapExtension.setAllowedToSwap(pool, address(router), true)
4. Bob (not on the allowlist) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., amountIn: X, ...})
5. Router calls pool.swap() — msg.sender at the pool = address(router).
6. Pool calls _beforeSwap(msg.sender=router, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes on the curated pool despite never being allowlisted.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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
