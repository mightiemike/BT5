The call chain is fully traceable. Here is the definitive analysis.

---

## Call Chain Trace

```
User (non-allowlisted)
  → MetricOmmSimpleRouter::exactInputSingle()   [msg.sender = user]
      → MetricOmmPool::swap()                   [msg.sender = ROUTER]
          → _beforeSwap(msg.sender=ROUTER, ...)
              → ExtensionCalling::_beforeSwap(sender=ROUTER, ...)
                  → SwapAllowlistExtension::beforeSwap(sender=ROUTER, ...)
                      checks: allowedSwapper[pool][ROUTER]
```

**The hook never sees the original user.** It sees the router.

---

### The Exact Mismatch

In `MetricOmmPool::swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called the pool — i.e., the **router**, not the original user: [3](#0-2) 

The router calls the pool directly with no forwarding of the original caller: [4](#0-3) 

---

### Why This Is a Real Vulnerability

The pool admin's intent is to gate swaps by **user identity**. The extension's own NatSpec says *"Gates `swap` by swapper address, per pool."* [5](#0-4) 

But the check is structurally broken for router paths. The admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist the router | **Every user bypasses the allowlist** via the public router |
| Do not allowlist the router | **Allowlisted users cannot use the router at all** |

There is no configuration that makes the allowlist work correctly for router-mediated swaps. An unprivileged attacker needs only to call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public router — no privileged access, no malicious pool setup, no non-standard tokens.

---

### Regarding the Question's Other Claims

The question also mentions a "velocity-envelope bypass" and "per-block price-change cap." **These do not exist in `SwapAllowlistExtension`.** The extension contains only an allowlist check — no price cap, no block-delta guard, no squared-envelope computation. That part of the question is fabricated framing and does not correspond to any code in scope.

The "remove-liquidity while paused" angle is also a non-issue: `swap` has a `whenNotPaused` modifier, so paused pools block swaps entirely regardless of extensions. [6](#0-5) 

---

### Valid Finding

### Title
SwapAllowlistExtension checks router address instead of user address, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` receives `sender` = the immediate caller of `MetricOmmPool::swap`. When a swap is routed through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. The hook therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any non-allowlisted user can bypass the gate by calling any `exact*` function on the public router.

### Finding Description
`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`. `ExtensionCalling` forwards it unchanged to the extension. `SwapAllowlistExtension::beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` — pool-keyed by the immediate caller of the pool. When the router is the immediate caller, `sender` is the router address. The original user's address is never visible to the hook.

### Impact Explanation
The swap allowlist is completely ineffective for router-mediated swaps. Any user the pool admin intended to block can swap freely by routing through `MetricOmmSimpleRouter`. This breaks the core access-control functionality of the extension and allows unauthorized parties to trade on restricted pools.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical public entrypoint for swaps. Any user aware of the router can exploit this with a single transaction. No privileged access, no special setup, no non-standard tokens required.

### Recommendation
Pass the original user's address through the call chain. One approach: add a `payer` or `originator` field to the `beforeSwap` hook signature that the router explicitly sets (e.g., via `extensionData`). Alternatively, the extension can read `tx.origin` as a fallback, though that has its own risks. The cleanest fix is for the pool or router to propagate the true initiator address as a distinct parameter separate from the immediate `msg.sender`.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter::exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Hook checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
7. Bob has swapped on a pool he was explicitly excluded from.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
