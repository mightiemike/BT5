### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the allowlist checks the router's address rather than the original user's address. If the router is allowlisted — a necessary configuration for any pool that wants to support router-mediated swaps for allowlisted users — any unprivileged user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap`, the `_beforeSwap` hook is dispatched with `msg.sender` (the immediate caller of `pool.swap`) forwarded as the `sender` argument: [1](#0-0) 

`_beforeSwap` then encodes and dispatches that `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives that value and checks it against the per-pool allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, `msg.sender` inside the pool is the router contract. The allowlist therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

A pool admin who wants to support router-mediated swaps for allowlisted users must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** user who routes through it, regardless of whether that user is individually permitted. The guard is silently nullified for the entire router-mediated path.

The `ISwapAllowlistExtension` interface and the `allowedSwapper` mapping are both keyed on `(pool, swapper)`: [4](#0-3) 

There is no mechanism to recover the original EOA from within the hook; the extension has no access to the router's internal call context.

The `generate_scanned_questions.py` research file explicitly identifies this as the critical invariant: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4) 

---

### Impact Explanation

The swap allowlist guard is completely ineffective for router-mediated swaps. Any unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. LPs in a restricted pool suffer direct losses from unauthorized trading — adverse selection, front-running, or targeted value extraction — by users the pool admin explicitly intended to exclude. The pool admin's access control is silently nullified with no on-chain indication of the bypass.

---

### Likelihood Explanation

Medium. The bypass requires the router to be allowlisted, which is a necessary and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users. Pool admins who configure both the allowlist and router support will inadvertently expose the bypass. The trigger is any unprivileged user calling the public router.

---

### Recommendation

The `SwapAllowlistExtension` must check the original user's address rather than the immediate caller. Two viable approaches:

1. **`extensionData` forwarding**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` for each hop, and have the extension decode and check that address. The pool's `_beforeSwap` already forwards `extensionData` unmodified to extensions.
2. **Separate router-aware allowlist**: Introduce a two-level check — if `sender` is a known router, decode the original user from `extensionData`; otherwise check `sender` directly.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — `alice` is a trusted trader.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow `alice` to use the router.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the restricted pool.
5. The router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)` with `msg.sender = router`.
6. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender=router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
8. The swap executes. `bob` successfully trades against the restricted pool, bypassing the allowlist entirely. [3](#0-2) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** generate_scanned_questions.py (L658-663)
```python
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
