The call chain is fully traceable. Here is the determination:

---

### Title
Router-Mediated Swaps Replace End-User Identity with Router Address in `SwapAllowlistExtension.beforeSwap`, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

When a swap is routed through `MetricOmmSimpleRouter`, the `sender` argument delivered to `SwapAllowlistExtension.beforeSwap` is the **router's address**, not the originating user's address. If the router is allowlisted for a curated pool, any unprivileged user can bypass the per-user allowlist by routing through the router.

### Finding Description

**Step 1 — Router calls the pool as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` (and all `exact*` variants) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. [1](#0-0) 

At this call site, `msg.sender` seen by the pool is the **router contract address**, not the originating EOA.

**Step 2 — Pool passes `msg.sender` (= router) as `sender` to the hook:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. [2](#0-1) 

**Step 3 — `ExtensionCalling._beforeSwap` forwards `sender` (= router) to the extension:** [3](#0-2) 

The `sender` argument encoded into the extension call is the router address.

**Step 4 — `SwapAllowlistExtension.beforeSwap` checks the router, not the user:**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

Here:
- `msg.sender` = pool address (correct — the pool calls the extension)
- `sender` = **router address** (wrong — should be the originating user)

The check `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

**Exploit scenario:**

A curated pool is deployed with `SwapAllowlistExtension`. The pool admin allowlists specific KYC'd users and also allowlists the router so those users can trade via the standard periphery. Because the hook only sees the router's address as `sender`, **any unprivileged user** can call `exactInputSingle` on the router and pass the allowlist check — the hook approves the router, not the individual user.

The allowlist mapping is keyed `allowedSwapper[pool][swapper]` and set only by `onlyPoolAdmin`. [5](#0-4) [6](#0-5) 

There is no mechanism in the router or the pool to propagate the originating user's address into the `sender` slot of the hook call.

### Impact Explanation

Any user not on the allowlist can trade in a curated pool by routing through `MetricOmmSimpleRouter`, completely defeating the access-control invariant the pool admin configured. This is broken core pool functionality: the allowlist gate is rendered ineffective for all router-mediated swaps.

### Likelihood Explanation

The router is the standard, documented periphery entrypoint. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router — a natural and expected configuration — is fully exposed. No privileged access or malicious setup is required; any user can exploit this by simply calling the public router.

### Recommendation

The router should pass the originating user's address to the pool in a way the extension can recover it. The cleanest approach is for the router to encode `msg.sender` into `extensionData` and for `SwapAllowlistExtension.beforeSwap` to decode and use it when the direct `sender` is a known router. Alternatively, the pool's hook interface should carry a separate `origin` field distinct from `sender` (the immediate caller), analogous to Solidity's `tx.origin` but passed explicitly and verifiably through the call chain.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so KYC'd users can trade via periphery.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is the only intended user.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Bob successfully swaps in a pool he was never authorized to access.

The concrete assertion: `allowedSwapper[pool][bob] == false` yet Bob's swap succeeds because `allowedSwapper[pool][router] == true` and the hook never inspects Bob's address.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
