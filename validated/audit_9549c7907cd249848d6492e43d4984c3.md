The call chain is fully traceable. Here is the exact flow and the real issue:

**Call chain:**
1. User → `MetricOmmSimpleRouter::exactInputSingle` (`msg.sender` = User)
2. Router → `MetricOmmPool::swap(...)` (`msg.sender` to pool = **Router address**)
3. Pool → `_beforeSwap(msg.sender=Router, ...)` [1](#0-0) 
4. `ExtensionCalling::_beforeSwap` encodes `sender=Router` and calls the extension [2](#0-1) 
5. `SwapAllowlistExtension::beforeSwap` checks `allowedSwapper[msg.sender=pool][sender=Router]` [3](#0-2) 

The `sender` the hook receives is always the **router's address**, never the original user's address. The `extensionData` bytes are received but entirely ignored by the hook. [4](#0-3) 

---

### Title
Router-Mediated Swaps Corrupt Swapper Identity in `SwapAllowlistExtension::beforeSwap`, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` gates swaps using the `sender` argument, which is `msg.sender` of `MetricOmmPool::swap`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the original user. The hook therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router address to enable router-mediated swaps for specific users, every user on the network can bypass the per-user allowlist by routing through the router.

### Finding Description

`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- always the direct caller (router when routed)
    recipient,
    ...
);
```

`ExtensionCalling::_beforeSwap` forwards this value verbatim to the extension. [5](#0-4) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The original user's address is never visible to the hook. [4](#0-3) 

`MetricOmmSimpleRouter::exactInputSingle` (and all other `exact*` entry points) calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [6](#0-5) 

There are two broken outcomes:

| Router allowlisted? | Result |
|---|---|
| **Yes** (admin adds router to allowlist so users can route) | Every user on the network can swap in the restricted pool — full allowlist bypass |
| **No** | Allowlisted users cannot use the router at all — core router functionality broken for allowlisted pools |

### Impact Explanation
If a pool admin allowlists the router to enable router-mediated swaps for specific counterparties, any unprivileged user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the hook will pass because it sees the router address, which is allowlisted. The per-user restriction is completely nullified. For pools designed for restricted institutional access or specific price-discovery counterparties, this allows unauthorized users to drain liquidity at the pool's quoted prices.

### Likelihood Explanation
Any pool that (a) uses `SwapAllowlistExtension` with per-user allowlisting and (b) needs to support router-mediated swaps will be affected. The admin has no way to simultaneously allow the router and restrict individual users — the two goals are mutually exclusive given the current design.

### Recommendation
Pass the original user's address through the hook system. One approach: add an `originator` field to the `beforeSwap` hook signature that the pool sets to `tx.origin` or, better, have the router encode the real user address in `extensionData` and have the extension decode and verify it (with a signature or trusted-router pattern). Alternatively, the pool should pass `tx.origin` as a separate argument alongside `sender` so extensions can choose which identity to gate on.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Call `setAllowedToSwap(pool, router, true)` — admin allowlists the router so that allowlisted users can use it.
3. As an unprivileged attacker (not in `allowedSwapper`), call `MetricOmmSimpleRouter::exactInputSingle` targeting that pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`, the hook checks `allowedSwapper[pool][router] == true`, and the swap succeeds.
5. The attacker has bypassed the per-user allowlist entirely.

The `extensionData` bytes passed by the router are ignored by the hook, so no payload manipulation is needed — the bypass is structural. [4](#0-3)

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
